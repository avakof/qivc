"""
SEC Form 25/15 delisting loader (Task 14 Phase B2).

Parses the EDGAR quarterly **full index** (``master.idx``, pipe-delimited) for
delisting/deregistration filings — Form 25 / 25-NSE (removal from listing) and
Form 15-12B / 15-12G / 15-15D (deregistration) — and stores them in
``delistings_historical``. Same bulk, idempotent, schema-checked discipline as the
Form 4 loader: a quarter is re-imported cleanly (delete-then-insert).

Reason note: Form 25/15 filings do **not** carry a clean machine-readable reason
(bankruptcy vs merger vs listing-standards). We therefore store ``reason =
'unknown'`` at load time; the conservative B3 default (-50%) applies, and a reason
can be enriched on demand via :func:`classify_reason` on the filing text for the
small set of names actually held-then-delisted in a backtest.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from dataclasses import dataclass

import httpx

from qivc.storage.db import get_connection

log = logging.getLogger(__name__)

_MASTER_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/master.idx"
_EXPECTED_HEADER = "CIK|Company Name|Form Type|Date Filed|Filename"
# Exact delisting/deregistration forms (and /A amendments). NOT 253G* (Reg-A).
_DELISTING_FORMS = re.compile(r"^(25|25-NSE|15-12B|15-12G|15-15D|15F-12B|15F-12G)(/A)?$")


@dataclass(frozen=True)
class DelistingRow:
    cik: str
    company_name: str
    form_type: str
    filed_date: _dt.date
    accession: str


def master_index_url(quarter: str) -> str:
    year, q = quarter.lower().split("q")
    return _MASTER_URL.format(year=int(year), q=int(q))


def parse_master_idx(text: str) -> list[DelistingRow]:
    """
    Parse master.idx, returning only delisting/deregistration rows.

    Schema-checked: the canonical 5-column pipe header must be present, else we
    raise (stop-and-explain) rather than silently mis-parse a changed format.
    """
    lines = text.splitlines()
    if not any(line.strip() == _EXPECTED_HEADER for line in lines[:15]):
        raise RuntimeError(
            "master.idx schema changed: expected header "
            f"'{_EXPECTED_HEADER}' in the first 15 lines — stop and re-check the format."
        )
    start = next(i for i, line in enumerate(lines) if line.strip() == _EXPECTED_HEADER) + 1
    rows: list[DelistingRow] = []
    for line in lines[start:]:
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, name, form_type, filed, filename = parts
        if not _DELISTING_FORMS.match(form_type.strip()):
            continue
        try:
            fd = _dt.date.fromisoformat(filed.strip())
        except ValueError:
            continue
        # filename: edgar/data/<cik>/<accession>.txt
        acc = filename.strip().rsplit("/", 1)[-1].removesuffix(".txt")
        rows.append(DelistingRow(cik.strip(), name.strip(), form_type.strip(), fd, acc))
    return rows


def load_delistings(
    db_path: str,
    quarters: list[str],
    user_agent: str,
    *,
    cik_to_ticker: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    """
    Load Form 25/15 delistings for *quarters* into delistings_historical.
    Idempotent per quarter. Returns {quarter: row_count}. *cik_to_ticker* (from
    SEC company_tickers.json) resolves a ticker where the issuer is still listed.
    """
    own = client is None
    client = client or httpx.Client(timeout=120.0)
    cik_to_ticker = cik_to_ticker or {}
    report: dict[str, int] = {}
    try:
        with get_connection(db_path) as conn:
            for quarter in quarters:
                resp = client.get(
                    master_index_url(quarter), headers={"User-Agent": user_agent}
                )
                resp.raise_for_status()
                rows = parse_master_idx(resp.text)
                conn.execute(
                    "DELETE FROM delistings_historical WHERE source_quarter = ?", [quarter]
                )
                conn.executemany(
                    """INSERT INTO delistings_historical
                       (cik, company_name, ticker, form_type, filed_date, reason,
                        accession, source_quarter)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    [
                        [r.cik, r.company_name, cik_to_ticker.get(str(int(r.cik))),
                         r.form_type, r.filed_date, "unknown", r.accession, quarter]
                        for r in rows
                    ],
                )
                conn.commit()
                report[quarter] = len(rows)
                log.info("delistings %s: %d Form 25/15 rows", quarter, len(rows))
    finally:
        if own:
            client.close()
    return report
