"""
Point-in-time IWM (Russell 2000) membership from iShares N-PORT filings (Task 14
Phase B1).

iShares Russell 2000 ETF (IWM) is a series of iShares Trust (CIK 0001100663),
which files NPORT-P with full holdings each quarter. The public June-30 filing
gives the index membership at the annual Russell reconstitution. N-PORT identifies
holdings by name / CUSIP / ISIN / LEI — NOT ticker — so we map CUSIP (then ISIN)
to a ticker via the free OpenFIGI service, caching every lookup to disk so re-runs
never re-hit the API.

WATCH-1 (survivor bias): a holding we cannot resolve to a ticker is stored with
``mapped_flag = FALSE`` and ``mapping_source = 'unmapped'`` — FLAGGED AND COUNTED,
never silently dropped. Dropping a delisted name would put the survivor bias right
back in, so the loader reports mapped/unmapped per year.

All network calls use plain httpx with the SEC user-agent (same as bulk_loader).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from qivc.storage.db import get_connection

log = logging.getLogger(__name__)

IWM_TRUST_CIK = "0001100663"
_FTS_URL = "https://efts.sec.gov/LATEST/search-index"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1100663/{acc}/{doc}"
_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
_OPENFIGI_BATCH = 10  # anonymous limit: 10 jobs/request
_OPENFIGI_SLEEP_S = 2.6  # stay under the anonymous ~25 req/min ceiling
DEFAULT_FIGI_CACHE = "data/pit/openfigi_cache.json"

_HOLDING_RE = re.compile(r"<invstOrSec>(.*?)</invstOrSec>", re.S)
_NAME_RE = re.compile(r"<name>(.*?)</name>", re.S)
_CUSIP_RE = re.compile(r"<cusip>(.*?)</cusip>", re.S)
_ISIN_RE = re.compile(r'isin value="(.*?)"')
_LEI_RE = re.compile(r"<lei>(.*?)</lei>", re.S)
_PLACEHOLDER_CUSIPS = {"000000000", "N/A", ""}


@dataclass(frozen=True)
class NportHolding:
    name: str
    cusip: str | None
    isin: str | None
    lei: str | None


@dataclass(frozen=True)
class MappedHolding:
    name: str
    cusip: str | None
    isin: str | None
    ticker: str | None
    mapping_source: str  # openfigi_cusip | openfigi_isin | unmapped
    mapped: bool


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------
def parse_holdings(xml: str) -> list[NportHolding]:
    """Parse N-PORT primary_doc.xml holdings (CONTRA corporate-action rows skipped)."""
    out: list[NportHolding] = []
    for block in _HOLDING_RE.findall(xml):
        nm = _NAME_RE.search(block)
        if nm is None or "CONTRA" in nm.group(1).upper():
            continue
        cu = _CUSIP_RE.search(block)
        isin = _ISIN_RE.search(block)
        lei = _LEI_RE.search(block)
        cusip = cu.group(1).strip() if cu else None
        if cusip in _PLACEHOLDER_CUSIPS:
            cusip = None
        out.append(
            NportHolding(
                name=nm.group(1).strip(),
                cusip=cusip,
                isin=isin.group(1).strip() if isin else None,
                lei=lei.group(1).strip() if lei else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# OpenFIGI mapping (cached; fetch fn is injectable for tests)
# ---------------------------------------------------------------------------
def _openfigi_fetch(jobs: list[dict[str, str]], client: httpx.Client) -> list[dict[str, Any]]:
    """POST a batch to OpenFIGI; returns the parallel results list."""
    resp = client.post(_OPENFIGI_URL, json=jobs, headers={"Content-Type": "application/json"})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


def map_identifiers(
    holdings: list[NportHolding],
    cache: dict[str, str | None],
    *,
    fetch: Callable[[list[dict[str, str]]], list[dict[str, Any]]] | None = None,
) -> list[MappedHolding]:
    """
    Resolve each holding to a ticker via OpenFIGI, CUSIP first then ISIN. *cache*
    maps "ID_CUSIP:<v>"/"ID_ISIN:<v>" -> ticker|None and is mutated in place
    (persist it to disk so re-runs skip the API). *fetch* (a batch->results fn)
    is injectable; when None a live, rate-limited httpx client is used.
    """
    def key(idtype: str, idval: str) -> str:
        return f"{idtype}:{idval}"

    def resolve(needed: list[tuple[str, str]]) -> None:
        if not needed:
            return
        if fetch is not None:
            _resolve_batches(needed, cache, fetch, rate_limit=False)
            return
        client = httpx.Client(timeout=30.0)
        try:
            _resolve_batches(
                needed, cache, lambda jobs: _openfigi_fetch(jobs, client), rate_limit=True
            )
        finally:
            client.close()

    # Pass 1: CUSIP where present, else ISIN. (Querying both for every holding
    # would double the rate-limited calls; CUSIP resolves ~all names with one.)
    pass1 = [
        (idtype, idval)
        for h in holdings
        for idtype, idval in [("ID_CUSIP", h.cusip) if h.cusip else ("ID_ISIN", h.isin)]
        if idval and key(idtype, idval) not in cache
    ]
    resolve(pass1)

    # Pass 2: ISIN fallback ONLY for holdings whose CUSIP failed to map (rare),
    # so a CUSIP miss never silently becomes "unmapped" when an ISIN would work.
    pass2 = [
        ("ID_ISIN", h.isin)
        for h in holdings
        if h.cusip
        and not cache.get(key("ID_CUSIP", h.cusip))
        and h.isin
        and key("ID_ISIN", h.isin) not in cache
    ]
    resolve(pass2)

    out: list[MappedHolding] = []
    for h in holdings:
        ticker: str | None = None
        source = "unmapped"
        if h.cusip and cache.get(key("ID_CUSIP", h.cusip)):
            ticker, source = cache[key("ID_CUSIP", h.cusip)], "openfigi_cusip"
        elif h.isin and cache.get(key("ID_ISIN", h.isin)):
            ticker, source = cache[key("ID_ISIN", h.isin)], "openfigi_isin"
        out.append(
            MappedHolding(h.name, h.cusip, h.isin, ticker, source, ticker is not None)
        )
    return out


def _resolve_batches(
    needed: list[tuple[str, str]],
    cache: dict[str, str | None],
    fetch: Callable[[list[dict[str, str]]], list[dict[str, Any]]],
    *,
    rate_limit: bool,
) -> None:
    for i in range(0, len(needed), _OPENFIGI_BATCH):
        batch = needed[i : i + _OPENFIGI_BATCH]
        jobs = [{"idType": t, "idValue": v} for t, v in batch]
        try:
            results = fetch(jobs)
        except Exception as exc:  # network/transient — leave uncached, retry next run
            log.warning("openfigi batch failed (%d jobs): %s", len(jobs), exc)
            results = [{} for _ in batch]
        for (idtype, idval), res in zip(batch, results, strict=True):
            data = res.get("data") if isinstance(res, dict) else None
            tk: str | None = None
            if data:
                us = next((d for d in data if d.get("exchCode") == "US"), data[0])
                tk = us.get("ticker")
            cache[f"{idtype}:{idval}"] = tk
        if rate_limit and i + _OPENFIGI_BATCH < len(needed):
            time.sleep(_OPENFIGI_SLEEP_S)


# ---------------------------------------------------------------------------
# EDGAR fetch
# ---------------------------------------------------------------------------
def find_june_nport(
    year: int, user_agent: str, client: httpx.Client
) -> tuple[str, str, _dt.date | None] | None:
    """Return (accession, primary_doc_name, file_date) for IWM's {year}-06-30 NPORT-P."""
    resp = client.get(
        _FTS_URL,
        params={"q": '"iShares Russell 2000 ETF"', "forms": "NPORT-P", "ciks": IWM_TRUST_CIK},
        headers={"User-Agent": user_agent},
    )
    resp.raise_for_status()
    for hit in resp.json().get("hits", {}).get("hits", []):
        if hit["_source"].get("period_ending") == f"{year}-06-30":
            acc, doc = hit["_id"].split(":", 1)
            fd_raw = hit["_source"].get("file_date")
            fd = _dt.date.fromisoformat(fd_raw) if fd_raw else None
            return acc, doc, fd
    return None


def fetch_nport_xml(accession: str, doc: str, user_agent: str, client: httpx.Client) -> str:
    """Fetch the N-PORT XML. Prefers primary_doc.xml; falls back to the named doc."""
    acc_nodash = accession.replace("-", "")
    for name in ("primary_doc.xml", doc):
        url = _ARCHIVE.format(acc=acc_nodash, doc=name)
        try:
            resp = client.get(url, headers={"User-Agent": user_agent})
            if resp.status_code == 200 and "<invstOrSec>" in resp.text:
                return resp.text
        except httpx.HTTPError as exc:
            log.warning("nport fetch %s failed: %s", url, exc)
    raise RuntimeError(f"could not fetch N-PORT holdings XML for {accession}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def load_pit_membership(
    db_path: str,
    years: list[int],
    user_agent: str,
    *,
    figi_cache_path: str = DEFAULT_FIGI_CACHE,
    client: httpx.Client | None = None,
) -> dict[int, dict[str, int]]:
    """
    Load IWM PIT membership for each June-30 of *years* into iwm_pit_constituents.
    Idempotent per as_of_date. Returns {year: {mapped, unmapped, total}}.
    """
    own = client is None
    client = client or httpx.Client(timeout=60.0)
    cache_path = Path(figi_cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache: dict[str, str | None] = (
        json.loads(cache_path.read_text()) if cache_path.exists() else {}
    )
    report: dict[int, dict[str, int]] = {}
    try:
        with get_connection(db_path) as conn:
            for year in years:
                found = find_june_nport(year, user_agent, client)
                if found is None:
                    log.warning("no IWM NPORT-P found for %d-06-30", year)
                    continue
                accession, doc, filed = found
                xml = fetch_nport_xml(accession, doc, user_agent, client)
                holdings = parse_holdings(xml)
                mapped = map_identifiers(holdings, cache)
                cache_path.write_text(json.dumps(cache, indent=0, sort_keys=True))
                as_of = _dt.date(year, 6, 30)
                conn.execute("DELETE FROM iwm_pit_constituents WHERE as_of_date = ?", [as_of])
                conn.executemany(
                    """INSERT INTO iwm_pit_constituents
                       (as_of_date, ticker, cusip, isin, name, mapping_source,
                        mapped_flag, filed_date, accession)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    [
                        [as_of, m.ticker, m.cusip, m.isin, m.name, m.mapping_source,
                         m.mapped, filed, accession]
                        for m in mapped
                    ],
                )
                conn.commit()
                n_map = sum(1 for m in mapped if m.mapped)
                report[year] = {
                    "total": len(mapped), "mapped": n_map, "unmapped": len(mapped) - n_map,
                }
                log.info(
                    "IWM %d-06-30: %d holdings, %d mapped, %d UNMAPPED (flagged)",
                    year, len(mapped), n_map, len(mapped) - n_map,
                )
    finally:
        if own:
            client.close()
    return report
