"""Tests for the N-PORT membership + Form 25/15 delisting loaders (Task 14 B1/B2)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest

from qivc.data import delisting_loader, nport_loader
from qivc.data.nport_loader import NportHolding, map_identifiers, parse_holdings
from qivc.storage.db import get_connection

_NPORT_XML = """
<edgarSubmission>
 <invstOrSec><name>Energy Vault Holdings Inc</name><lei>X1</lei>
   <cusip>29280W109</cusip><identifiers><isin value="US29280W1099"/></identifiers></invstOrSec>
 <invstOrSec><name>Placeholder Co</name><cusip>000000000</cusip>
   <identifiers><isin value="US0000000000"/></identifiers></invstOrSec>
 <invstOrSec><name>CONTRA ACME</name><cusip>111111111</cusip></invstOrSec>
 <invstOrSec><name>NoMap Industries Inc</name><cusip>999999999</cusip></invstOrSec>
</edgarSubmission>
"""


def test_parse_holdings_skips_contra_and_placeholder_cusip() -> None:
    h = parse_holdings(_NPORT_XML)
    names = [x.name for x in h]
    assert "CONTRA ACME" not in names           # corporate-action placeholder skipped
    assert len(h) == 3
    ev = next(x for x in h if x.name.startswith("Energy"))
    assert ev.cusip == "29280W109" and ev.isin == "US29280W1099"
    ph = next(x for x in h if x.name == "Placeholder Co")
    assert ph.cusip is None and ph.isin == "US0000000000"  # placeholder CUSIP nulled, ISIN kept


def test_map_identifiers_cusip_then_isin_then_unmapped_flagged() -> None:
    holdings = [
        NportHolding("Energy Vault", "29280W109", "US29280W1099", None),
        NportHolding("Placeholder", None, "US0000000000", None),  # ISIN fallback
        NportHolding("NoMap", "999999999", None, None),           # OpenFIGI returns nothing
    ]

    def fake_fetch(jobs: list[dict[str, str]]) -> list[dict]:
        out = []
        for j in jobs:
            if j["idValue"] == "29280W109":
                out.append({"data": [{"ticker": "NRGV", "exchCode": "US"}]})
            elif j["idValue"] == "US0000000000":
                out.append({"data": [{"ticker": "PLAC", "exchCode": "US"}]})
            else:
                out.append({"warning": "No identifier found."})  # no 'data'
        return out

    cache: dict[str, str | None] = {}
    mapped = map_identifiers(holdings, cache, fetch=fake_fetch)
    by = {m.name: m for m in mapped}
    assert by["Energy Vault"].ticker == "NRGV"
    assert by["Energy Vault"].mapping_source == "openfigi_cusip"
    assert by["Placeholder"].ticker == "PLAC"
    assert by["Placeholder"].mapping_source == "openfigi_isin"
    # UNMAPPED is flagged + counted, never silently dropped
    assert by["NoMap"].mapped is False and by["NoMap"].ticker is None
    assert by["NoMap"].mapping_source == "unmapped"
    assert len(mapped) == 3  # the unmapped holding is still present


def test_map_identifiers_uses_cache_no_refetch() -> None:
    holdings = [NportHolding("X", "29280W109", None, None)]
    cache = {"ID_CUSIP:29280W109": "NRGV"}
    calls = []

    def fetch(jobs: list[dict[str, str]]) -> list[dict]:
        calls.append(jobs)
        return [{"data": [{"ticker": "WRONG", "exchCode": "US"}]}]

    mapped = map_identifiers(holdings, cache, fetch=fetch)
    assert mapped[0].ticker == "NRGV"  # from cache
    assert calls == []                  # API not hit for a cached id


# ---------------------------------------------------------------------------
# Form 25/15 master.idx parsing + schema-check
# ---------------------------------------------------------------------------
_MASTER = """Description:           Master Index
Comments:              webmaster@sec.gov

CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
111|ALPHA CORP|25-NSE|2022-06-15|edgar/data/111/0000111-22-000001.txt
222|BETA INC|15-12B|2022-06-20|edgar/data/222/0000222-22-000002.txt
333|GAMMA REGA|253G2|2022-06-21|edgar/data/333/0000333-22-000003.txt
444|DELTA LLC|10-K|2022-06-22|edgar/data/444/0000444-22-000004.txt
555|EPSILON CO|25|2022-06-23|edgar/data/555/0000555-22-000005.txt
"""


def test_parse_master_idx_filters_to_delisting_forms_only() -> None:
    rows = delisting_loader.parse_master_idx(_MASTER)
    forms = {r.form_type for r in rows}
    assert forms == {"25-NSE", "15-12B", "25"}   # 253G2 (Reg-A) + 10-K excluded
    alpha = next(r for r in rows if r.cik == "111")
    assert alpha.accession == "0000111-22-000001" and alpha.filed_date == dt.date(2022, 6, 15)


def test_parse_master_idx_schema_check_raises_on_changed_header() -> None:
    bad = "CIK;Company;Form;Date;File\n111;A;25;2022-01-01;x.txt\n"
    with pytest.raises(RuntimeError, match="schema changed"):
        delisting_loader.parse_master_idx(bad)


def test_load_delistings_idempotent(tmp_path: Path) -> None:
    db = str(tmp_path / "d.db")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_MASTER)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    r1 = delisting_loader.load_delistings(db, ["2022q2"], "ua", client=client)
    r2 = delisting_loader.load_delistings(db, ["2022q2"], "ua", client=client)  # re-run
    assert r1 == r2 == {"2022q2": 3}
    with get_connection(db) as conn:
        n = conn.execute("SELECT count(*) FROM delistings_historical").fetchone()[0]
    assert n == 3  # re-run did NOT duplicate (delete-then-insert per quarter)


def test_load_pit_membership_end_to_end_flags_unmapped(tmp_path: Path) -> None:
    db = str(tmp_path / "m.db")
    fts = {
        "hits": {"hits": [
            {"_id": "0000-22-1:primary_doc.xml",
             "_source": {"period_ending": "2022-06-30", "file_date": "2022-08-25"}}
        ]}
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "search-index" in str(request.url):
            return httpx.Response(200, json=fts)
        if "primary_doc.xml" in str(request.url):
            return httpx.Response(200, text=_NPORT_XML)
        if "openfigi" in str(request.url):
            import json
            jobs = json.loads(request.content)
            out = []
            for j in jobs:
                if j["idValue"] == "29280W109":
                    out.append({"data": [{"ticker": "NRGV", "exchCode": "US"}]})
                else:
                    out.append({"warning": "no match"})
            return httpx.Response(200, json=out)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # monkeypatch the live OpenFIGI client path to use our mock client
    import qivc.data.nport_loader as nl
    orig = nl._openfigi_fetch
    nl._openfigi_fetch = lambda jobs, _c: client.post(  # type: ignore[assignment]
        nl._OPENFIGI_URL, json=jobs).json()
    try:
        report = nport_loader.load_pit_membership(
            db, [2022], "ua", figi_cache_path=str(tmp_path / "figi.json"), client=client
        )
    finally:
        nl._openfigi_fetch = orig  # type: ignore[assignment]

    assert report[2022]["total"] == 3
    assert report[2022]["mapped"] == 1      # only Energy Vault -> NRGV
    assert report[2022]["unmapped"] == 2    # the others flagged, not dropped
    with get_connection(db) as conn:
        total = conn.execute("SELECT count(*) FROM iwm_pit_constituents").fetchone()[0]
        unmapped = conn.execute(
            "SELECT count(*) FROM iwm_pit_constituents WHERE NOT mapped_flag"
        ).fetchone()[0]
    assert total == 3 and unmapped == 2  # unmapped rows persisted (counted, not dropped)
