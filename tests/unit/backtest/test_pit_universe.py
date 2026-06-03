"""Tests for the PIT universe providers + conservative delisting returns (Task 14 B3/B4)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from qivc.backtest.delisting_returns import (
    DELISTING_MULTIPLIER,
    classify_reason,
    delisting_exit_price,
)
from qivc.backtest.pit_universe import (
    BiasedSnapshot,
    PitUniverseProvider,
    SecPitProvider,
)
from qivc.storage.db import get_connection


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "pit.db")


def _add_snapshot(db: str, as_of: dt.date, filed: dt.date, tickers: list[str]) -> None:
    with get_connection(db) as conn:
        conn.execute("DELETE FROM iwm_pit_constituents WHERE as_of_date = ?", [as_of])
        conn.executemany(
            """INSERT INTO iwm_pit_constituents
               (as_of_date, ticker, cusip, isin, name, mapping_source, mapped_flag,
                filed_date, accession) VALUES (?,?,?,?,?,?,?,?,?)""",
            [[as_of, t, None, None, f"{t} Inc", "openfigi_cusip", True, filed, "acc"]
             for t in tickers],
        )
        conn.commit()


# ---------------------------------------------------------------------------
# B3 — conservative delisting-return rules (each category)
# ---------------------------------------------------------------------------
def test_b3_exit_mark_per_reason_category() -> None:
    assert delisting_exit_price("bankruptcy", 10.0) == pytest.approx(1.0)   # -90%
    assert delisting_exit_price("acquisition", 10.0) == pytest.approx(10.0)  # last price
    assert delisting_exit_price("listing_violation", 10.0) == pytest.approx(10.0)  # floor
    assert delisting_exit_price("unknown", 10.0) == pytest.approx(5.0)       # -50%
    # an unrecognised label defaults to the conservative -50%
    assert delisting_exit_price("nonsense", 10.0) == pytest.approx(5.0)
    assert DELISTING_MULTIPLIER["bankruptcy"] == 0.10


def test_b3_classify_reason() -> None:
    assert classify_reason("Company filed for Chapter 11 bankruptcy") == "bankruptcy"
    assert classify_reason("completion of the merger with Acme") == "acquisition"
    assert classify_reason("failure to satisfy continued listing standard") == "listing_violation"
    assert classify_reason("") == "unknown"
    assert classify_reason(None) == "unknown"


# ---------------------------------------------------------------------------
# B4 — SecPitProvider
# ---------------------------------------------------------------------------
def test_providers_satisfy_protocol(tmp_path: Path) -> None:
    assert isinstance(SecPitProvider(_db(tmp_path)), PitUniverseProvider)
    assert isinstance(BiasedSnapshot({"AAA"}), PitUniverseProvider)


def test_nport_membership_no_lookahead(tmp_path: Path) -> None:
    """get_constituents uses the latest snapshot FILED STRICTLY BEFORE as_of."""
    db = _db(tmp_path)
    _add_snapshot(db, dt.date(2021, 6, 30), dt.date(2021, 8, 25), ["AAA", "BBB"])
    _add_snapshot(db, dt.date(2022, 6, 30), dt.date(2022, 8, 25), ["CCC", "DDD"])
    p = SecPitProvider(db)
    # A 2022-07-01 rebalance: the 2022 N-PORT (filed 2022-08-25) is NOT YET filed,
    # so we must fall back to the 2021 snapshot — never the future filing.
    assert p.get_constituents(dt.date(2022, 7, 1)) == {"AAA", "BBB"}
    # After the 2022 filing date, the 2022 snapshot is available.
    assert p.get_constituents(dt.date(2022, 9, 1)) == {"CCC", "DDD"}
    # Before any filing exists -> empty.
    assert p.get_constituents(dt.date(2021, 1, 1)) == set()


def test_sec_provider_universe_differs_by_year(tmp_path: Path) -> None:
    """The survivor-bias fix actually changes the universe across years."""
    db = _db(tmp_path)
    _add_snapshot(db, dt.date(2022, 6, 30), dt.date(2022, 8, 25), ["AAA", "BBB", "GONE"])
    _add_snapshot(db, dt.date(2025, 6, 30), dt.date(2025, 8, 25), ["AAA", "BBB", "NEW"])
    p = SecPitProvider(db)
    u2022 = p.get_constituents(dt.date(2022, 9, 1))
    u2025 = p.get_constituents(dt.date(2025, 9, 1))
    assert u2022 != u2025
    assert "GONE" in u2022 and "GONE" not in u2025
    assert "NEW" in u2025 and "NEW" not in u2022


def test_held_then_bankrupt_records_minus_90(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_snapshot(db, dt.date(2022, 6, 30), dt.date(2022, 8, 25), ["ZOMBIE"])
    with get_connection(db) as conn:
        conn.execute(
            """INSERT INTO delistings_historical
               (cik, company_name, ticker, form_type, filed_date, reason, accession,
                source_quarter) VALUES (?,?,?,?,?,?,?,?)""",
            ["999", "ZOMBIE Inc", "ZOMBIE", "25-NSE", dt.date(2022, 9, 15),
             "bankruptcy", "acc", "2022q3"],
        )
        conn.commit()
    p = SecPitProvider(db)
    # held 2022-09-01 .. 2022-10-01, delists 2022-09-15 (in window) -> -90%
    exit_px = p.delisted_exit_price("ZOMBIE", 20.0, dt.date(2022, 9, 1), dt.date(2022, 10, 1))
    assert exit_px == pytest.approx(2.0)  # 20 * 0.10
    # outside the hold window -> no mark
    assert p.delisted_exit_price("ZOMBIE", 20.0, dt.date(2022, 10, 2), dt.date(2022, 11, 1)) is None
    # a name with no delisting -> None
    assert p.get_delisting_event("HEALTHY") is None


def test_delisting_event_matches_by_name_when_ticker_unresolved(tmp_path: Path) -> None:
    """Delisted names (no current ticker on the delisting row) link via company name."""
    db = _db(tmp_path)
    _add_snapshot(db, dt.date(2022, 6, 30), dt.date(2022, 8, 25), ["TUP"])
    with get_connection(db) as conn:
        # delisting row has NULL ticker (issuer no longer in company_tickers)
        conn.execute(
            """INSERT INTO delistings_historical
               (cik, company_name, ticker, form_type, filed_date, reason, accession,
                source_quarter) VALUES (?,?,?,?,?,?,?,?)""",
            ["123", "TUP INC", None, "25-NSE", dt.date(2024, 9, 1), "unknown", "a", "2024q3"],
        )
        conn.commit()
    ev = SecPitProvider(db).get_delisting_event("TUP")
    assert ev is not None and ev.company_name == "TUP INC"


def test_biased_snapshot_same_every_date_and_no_delistings() -> None:
    p = BiasedSnapshot({"AAA", "BBB"})
    assert p.get_constituents(dt.date(2022, 1, 1)) == {"AAA", "BBB"}
    assert p.get_constituents(dt.date(2025, 1, 1)) == {"AAA", "BBB"}
    assert p.get_delisting_event("AAA") is None  # the bias: delistings invisible
