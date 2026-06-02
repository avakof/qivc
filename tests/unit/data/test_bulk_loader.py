"""Unit tests for the bulk Form 4 historical loader (Task 8.1)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from qivc.data import bulk_loader as bl
from qivc.storage.db import get_connection

_SUBMISSION_HEADER = "ACCESSION_NUMBER\tFILING_DATE\tISSUERTRADINGSYMBOL"
_OWNER_HEADER = "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE"
_TRANS_HEADER = "ACCESSION_NUMBER\tTRANS_CODE\tTRANS_DATE\tTRANS_SHARES\tTRANS_PRICEPERSHARE"


def _write_tsv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _fixture(
    tmp_path: Path, submission: list[str], owner: list[str], trans: list[str]
) -> tuple[Path, Path, Path]:
    sub = tmp_path / "SUBMISSION.tsv"
    own = tmp_path / "REPORTINGOWNER.tsv"
    nd = tmp_path / "NONDERIV_TRANS.tsv"
    _write_tsv(sub, _SUBMISSION_HEADER, submission)
    _write_tsv(own, _OWNER_HEADER, owner)
    _write_tsv(nd, _TRANS_HEADER, trans)
    return sub, own, nd


# ---------------------------------------------------------------------------
# Quarter arithmetic
# ---------------------------------------------------------------------------


def test_parse_quarter_and_end_date() -> None:
    assert bl.parse_quarter("2024q1") == (2024, 1)
    assert bl.parse_quarter("2023Q4") == (2023, 4)
    assert bl.quarter_end_date("2024q1") == dt.date(2024, 3, 31)
    assert bl.quarter_end_date("2024q2") == dt.date(2024, 6, 30)
    assert bl.quarter_end_date("2024q3") == dt.date(2024, 9, 30)
    assert bl.quarter_end_date("2024q4") == dt.date(2024, 12, 31)


def test_parse_quarter_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        bl.parse_quarter("2024")
    with pytest.raises(ValueError):
        bl.parse_quarter("2024q5")


def test_next_quarter_rolls_year() -> None:
    assert bl.next_quarter("2024q1") == "2024q2"
    assert bl.next_quarter("2024q4") == "2025q1"


def test_quarters_in_range_inclusive() -> None:
    assert bl.quarters_in_range("2023q1", "2023q1") == ["2023q1"]
    assert bl.quarters_in_range("2023q3", "2024q2") == [
        "2023q3",
        "2023q4",
        "2024q1",
        "2024q2",
    ]


def test_latest_expected_quarter_respects_publication_lag() -> None:
    # 2026q1 ends 2026-03-31; +21d lag => available from 2026-04-21.
    assert bl.latest_expected_quarter(dt.date(2026, 4, 25)) == "2026q1"
    # Just before the lag elapses, the prior quarter is the latest expected.
    assert bl.latest_expected_quarter(dt.date(2026, 4, 10)) == "2025q4"


def test_archive_url() -> None:
    assert bl.archive_url("2024q1").endswith("insider-transactions-data-sets/2024q1_form345.zip")


# ---------------------------------------------------------------------------
# import_quarter: strict filter, flag derivation, fan-out, idempotency
# ---------------------------------------------------------------------------


def test_strict_filter_keeps_only_valid_purchases(tmp_path: Path) -> None:
    """Only code-P rows with positive price AND positive shares survive."""
    sub, own, nd = _fixture(
        tmp_path,
        submission=["A1\t07-FEB-2024\tAAA"],
        owner=["A1\t111\tALICE CEO\tOfficer\tChief Executive Officer"],
        trans=[
            "A1\tP\t05-FEB-2024\t1000.0\t50.0",  # valid purchase  -> kept
            "A1\tS\t05-FEB-2024\t1000.0\t50.0",  # sale            -> dropped (not P)
            "A1\tP\t05-FEB-2024\t1000.0\t0.0",  # zero price      -> dropped
            "A1\tP\t05-FEB-2024\t0.0\t50.0",  # zero shares     -> dropped
            "A1\tP\t05-FEB-2024\t\t50.0",  # blank shares    -> dropped
        ],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        stats = bl.import_quarter(conn, sub, own, nd, "2024q1")
        rows = conn.execute("SELECT shares, price, value_usd FROM form4_historical").fetchall()

    assert stats.total_p_transactions == 4  # four P rows (one S excluded from the count)
    assert stats.kept_p_transactions == 1
    assert stats.filtered_out == 3
    assert stats.records_inserted == 1
    assert len(stats.filtered_samples) == 3
    assert rows == [(1000.0, 50.0, 50_000.0)]


def test_relationship_flags_derived_from_text(tmp_path: Path) -> None:
    """is_director/is_officer/is_ten_percent_owner parsed from the combined text field."""
    sub, own, nd = _fixture(
        tmp_path,
        submission=["A1\t07-FEB-2024\tAAA"],
        owner=["A1\t111\tBOB\tDirector,Officer,TenPercentOwner\tPresident"],
        trans=["A1\tP\t05-FEB-2024\t10.0\t5.0"],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        bl.import_quarter(conn, sub, own, nd, "2024q1")
        row = conn.execute(
            "SELECT is_director, is_officer, is_ten_percent_owner, title FROM form4_historical"
        ).fetchone()
    assert row == (True, True, True, "President")


def test_relationship_flags_director_only(tmp_path: Path) -> None:
    sub, own, nd = _fixture(
        tmp_path,
        submission=["A1\t07-FEB-2024\tAAA"],
        owner=["A1\t111\tBOB\tDirector\t"],
        trans=["A1\tP\t05-FEB-2024\t10.0\t5.0"],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        bl.import_quarter(conn, sub, own, nd, "2024q1")
        row = conn.execute(
            "SELECT is_director, is_officer, is_ten_percent_owner FROM form4_historical"
        ).fetchone()
    assert row == (True, False, False)


def test_multi_owner_accession_fans_out(tmp_path: Path) -> None:
    """A single P transaction on a 3-owner accession yields 3 records (cross-join)."""
    sub, own, nd = _fixture(
        tmp_path,
        submission=["A1\t07-FEB-2024\tAAA"],
        owner=[
            "A1\t111\tFUND I LP\tTenPercentOwner\t",
            "A1\t222\tFUND GP LLC\tTenPercentOwner\t",
            "A1\t333\tMANAGER\tTenPercentOwner\t",
        ],
        trans=["A1\tP\t05-FEB-2024\t1000.0\t10.0"],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        stats = bl.import_quarter(conn, sub, own, nd, "2024q1")
        ciks = {r[0] for r in conn.execute("SELECT cik FROM form4_historical").fetchall()}
    # One transaction, three owners -> three records (the documented over-count).
    assert stats.kept_p_transactions == 1
    assert stats.records_inserted == 3
    assert ciks == {"111", "222", "333"}


def test_import_is_idempotent(tmp_path: Path) -> None:
    """Re-importing the same quarter does not duplicate rows."""
    sub, own, nd = _fixture(
        tmp_path,
        submission=["A1\t07-FEB-2024\tAAA"],
        owner=["A1\t111\tALICE\tOfficer\tCEO"],
        trans=["A1\tP\t05-FEB-2024\t1000.0\t50.0"],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        bl.import_quarter(conn, sub, own, nd, "2024q1")
        bl.import_quarter(conn, sub, own, nd, "2024q1")
        n = conn.execute("SELECT count(*) FROM form4_historical").fetchone()
    assert n == (1,)


def test_dates_parsed_from_ddmonyyyy(tmp_path: Path) -> None:
    sub, own, nd = _fixture(
        tmp_path,
        submission=["A1\t07-FEB-2024\tAAA"],
        owner=["A1\t111\tALICE\tOfficer\tCEO"],
        trans=["A1\tP\t05-FEB-2024\t1000.0\t50.0"],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        bl.import_quarter(conn, sub, own, nd, "2024q1")
        row = conn.execute("SELECT transaction_date, filed_date FROM form4_historical").fetchone()
    assert row == (dt.date(2024, 2, 5), dt.date(2024, 2, 7))


# ---------------------------------------------------------------------------
# load_quarter / progress ledger / refresh (no real network)
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self) -> _FakeStreamResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, chunk_size: int = 1) -> list[bytes]:
        return [self._data]


def _build_zip_bytes() -> bytes:
    """A minimal in-memory archive with the three TSVs the loader extracts."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SUBMISSION.tsv", _SUBMISSION_HEADER + "\nA1\t07-FEB-2024\tAAA\n")
        zf.writestr("REPORTINGOWNER.tsv", _OWNER_HEADER + "\nA1\t111\tALICE\tOfficer\tCEO\n")
        zf.writestr("NONDERIV_TRANS.tsv", _TRANS_HEADER + "\nA1\tP\t05-FEB-2024\t1000.0\t50.0\n")
    return buf.getvalue()


class _FakeClient:
    """Stand-in for httpx.Client: serves a canned archive, records calls."""

    def __init__(self, zip_bytes: bytes, exists: bool = True) -> None:
        self._zip = zip_bytes
        self._exists = exists
        self.stream_calls = 0

    def stream(self, method: str, url: str, headers: dict[str, str]) -> _FakeStreamResponse:
        self.stream_calls += 1
        return _FakeStreamResponse(self._zip)

    def get(self, url: str, headers: dict[str, str]) -> object:
        return type("R", (), {"status_code": 200 if self._exists else 403})()

    def close(self) -> None:
        return None


def test_load_quarter_imports_and_marks_complete(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    cache = tmp_path / "cache"
    client = _FakeClient(_build_zip_bytes())
    now = dt.datetime(2026, 5, 1, 12, 0, 0)
    with get_connection(db) as conn:
        stats = bl.load_quarter(conn, "2024q1", cache, "UA test", now, client=client)  # type: ignore[arg-type]
        prog = conn.execute(
            "SELECT quarter, record_count, status FROM bulk_load_progress"
        ).fetchone()
    assert stats.records_inserted == 1
    assert prog == ("2024q1", 1, "complete")
    # The extract scratch dir is cleaned up; only the cached zip remains.
    assert (cache / "2024q1_form345.zip").exists()


def test_load_quarter_skips_when_complete(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    cache = tmp_path / "cache"
    client = _FakeClient(_build_zip_bytes())
    now = dt.datetime(2026, 5, 1, 12, 0, 0)
    with get_connection(db) as conn:
        bl.load_quarter(conn, "2024q1", cache, "UA test", now, client=client)  # type: ignore[arg-type]
        first_calls = client.stream_calls
        stats = bl.load_quarter(conn, "2024q1", cache, "UA test", now, client=client)  # type: ignore[arg-type]
    assert stats.skipped is True
    assert client.stream_calls == first_calls  # no second download


def test_download_archive_reuses_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    client = _FakeClient(_build_zip_bytes())
    p1 = bl.download_archive("2024q1", cache, "UA", client=client)  # type: ignore[arg-type]
    p2 = bl.download_archive("2024q1", cache, "UA", client=client)  # type: ignore[arg-type]
    assert p1 == p2
    assert client.stream_calls == 1  # second call served from cache


def test_archive_exists_true_and_false() -> None:
    assert bl.archive_exists("2024q1", "UA", client=_FakeClient(b"", exists=True)) is True  # type: ignore[arg-type]
    assert bl.archive_exists("2099q1", "UA", client=_FakeClient(b"", exists=False)) is False  # type: ignore[arg-type]


def test_bootstrap_loads_requested_range(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    cache = tmp_path / "cache"
    client = _FakeClient(_build_zip_bytes())
    now = dt.datetime(2026, 5, 1, 12, 0, 0)
    result = bl.bootstrap(
        db,
        "UA",
        cache,
        now,
        start_quarter="2024q1",
        end_quarter="2024q2",
        client=client,  # type: ignore[arg-type]
    )
    assert [s.quarter for s in result.per_quarter] == ["2024q1", "2024q2"]
    # Two quarters, one record each (same canned archive) -> 2 total.
    assert result.total_records == 2


def test_refresh_loads_next_quarter_when_published(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    cache = tmp_path / "cache"
    client = _FakeClient(_build_zip_bytes(), exists=True)
    now = dt.datetime(2026, 5, 1, 12, 0, 0)
    # Seed: 2024q1 already complete; refresh should pull 2024q2.
    with get_connection(db) as conn:
        bl.load_quarter(conn, "2024q1", cache, "UA", now, client=client)  # type: ignore[arg-type]
    result = bl.refresh(db, "UA", cache, now, client=client)  # type: ignore[arg-type]
    assert [s.quarter for s in result.per_quarter] == ["2024q2"]
    assert "Loaded new quarter 2024q2" in result.message


def test_refresh_noop_when_next_quarter_unpublished(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    cache = tmp_path / "cache"
    now = dt.datetime(2026, 5, 1, 12, 0, 0)
    with get_connection(db) as conn:
        bl.load_quarter(conn, "2024q1", cache, "UA", now, client=_FakeClient(_build_zip_bytes()))  # type: ignore[arg-type]
    # archive_exists -> False, so nothing is loaded and a clean message is returned.
    result = bl.refresh(db, "UA", cache, now, client=_FakeClient(b"", exists=False))  # type: ignore[arg-type]
    assert result.per_quarter == []
    assert "not yet published" in result.message
    assert "expected after" in result.message


def test_refresh_message_when_nothing_loaded(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    cache = tmp_path / "cache"
    now = dt.datetime(2026, 5, 1, 12, 0, 0)
    result = bl.refresh(db, "UA", cache, now, client=_FakeClient(b""))  # type: ignore[arg-type]
    assert result.per_quarter == []
    assert "run a full bootstrap first" in result.message


def test_bootstrap_result_total_records_property() -> None:
    r = bl.BootstrapResult(
        per_quarter=[
            bl.ImportStats("2024q1", 10, 8, 2, 8),
            bl.ImportStats("2024q2", 5, 5, 0, 5),
        ]
    )
    assert r.total_records == 13


def test_validate_summarises_store(tmp_path: Path) -> None:
    sub, own, nd = _fixture(
        tmp_path,
        submission=["A1\t07-FEB-2024\tAAA", "A2\t07-FEB-2023\tBBB"],
        owner=["A1\t111\tALICE\tOfficer\tCEO", "A2\t222\tBOB\tDirector\t"],
        trans=[
            "A1\tP\t05-FEB-2024\t1000.0\t50.0",
            "A2\tP\t05-FEB-2023\t10.0\t5.0",
        ],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        bl.import_quarter(conn, sub, own, nd, "2024q1")
    v = bl.validate(db)
    assert v["total_records"] == 2
    assert v["distinct_tickers"] == 2
    assert v["min_filed_date"] == "2023-02-07"
    assert v["max_filed_date"] == "2024-02-07"
    assert (2023, 1) in v["per_year"]  # type: ignore[operator]
    assert (2024, 1) in v["per_year"]  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Read API: bulk_cutover_date + read_purchases (Task 8.2)
# ---------------------------------------------------------------------------


def test_bulk_cutover_date_is_latest_complete_quarter_end(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    now = dt.datetime(2026, 5, 1, 12, 0, 0)
    with get_connection(db) as conn:
        # No quarters yet -> None.
        assert bl.bulk_cutover_date(db) is None
        bl._record_progress(conn, "2025q4", 10, "complete", now)
        bl._record_progress(conn, "2026q1", 10, "complete", now)
        bl._record_progress(conn, "2026q2", 0, "failed", now)  # incomplete ignored
    # Latest COMPLETE quarter is 2026q1 -> end 2026-03-31.
    assert bl.bulk_cutover_date(db) == dt.date(2026, 3, 31)


def test_read_purchases_filters_by_date_and_ticker(tmp_path: Path) -> None:
    sub, own, nd = _fixture(
        tmp_path,
        submission=[
            "A1\t10-FEB-2024\tAAA",
            "A2\t20-FEB-2024\tBBB",
            "A3\t10-NOV-2024\tAAA",  # outside the Feb window
        ],
        owner=[
            "A1\t111\tALICE\tOfficer\tCEO",
            "A2\t222\tBOB\tDirector\t",
            "A3\t333\tCARL\tOfficer\tCFO",
        ],
        trans=[
            "A1\tP\t08-FEB-2024\t1000.0\t50.0",
            "A2\tP\t18-FEB-2024\t100.0\t10.0",
            "A3\tP\t08-NOV-2024\t100.0\t10.0",
        ],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        bl.import_quarter(conn, sub, own, nd, "2024q1")  # only Feb rows count here
        bl.import_quarter(conn, sub, own, nd, "2024q4")  # re-import keeps all 3 under q4

    # Window in Feb 2024 -> only the two Feb filings, regardless of source_quarter.
    feb = bl.read_purchases(db, dt.date(2024, 2, 1), dt.date(2024, 2, 28))
    assert {t.ticker for t in feb} == {"AAA", "BBB"}
    assert all(t.transaction_code == "P" for t in feb)
    assert all(t.accession_number for t in feb)  # provenance populated

    # Ticker scoping.
    aaa = bl.read_purchases(db, dt.date(2024, 2, 1), dt.date(2024, 2, 28), ticker="AAA")
    assert {t.ticker for t in aaa} == {"AAA"}


def test_read_purchases_returns_full_fanout(tmp_path: Path) -> None:
    """read_purchases preserves multi-owner fan-out (dedup is downstream)."""
    sub, own, nd = _fixture(
        tmp_path,
        submission=["A1\t10-FEB-2024\tAAA"],
        owner=[
            "A1\t111\tFUND LP\tTenPercentOwner\t",
            "A1\t222\tFUND GP\tTenPercentOwner\t",
        ],
        trans=["A1\tP\t08-FEB-2024\t1000.0\t10.0"],
    )
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        bl.import_quarter(conn, sub, own, nd, "2024q1")
    rows = bl.read_purchases(db, dt.date(2024, 2, 1), dt.date(2024, 2, 28))
    assert len(rows) == 2  # both co-owners present
    assert {r.cik for r in rows} == {"111", "222"}
    assert all(r.accession_number == "A1" for r in rows)
