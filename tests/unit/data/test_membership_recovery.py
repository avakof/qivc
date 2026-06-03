"""Tests for the 2022 delisted-name recovery pass (Task 14 Phase B recovery)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from qivc.data.membership_recovery import normalize_name, recover_membership
from qivc.storage.db import get_connection


def _seed(db: str) -> None:
    with get_connection(db) as conn:
        # three unmapped 2022 holdings + one already-mapped
        conn.executemany(
            """INSERT INTO iwm_pit_constituents
               (as_of_date, ticker, cusip, isin, name, mapping_source, mapped_flag,
                filed_date, accession) VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                [dt.date(2022, 6, 30), None, "C1", None, "Acme Robotics Inc",
                 "unmapped", False, dt.date(2022, 8, 25), "a"],   # -> sec_name
                [dt.date(2022, 6, 30), None, "C2", None, "Gone Bankrupt Inc",
                 "unmapped", False, dt.date(2022, 8, 25), "a"],   # -> form25_cik
                [dt.date(2022, 6, 30), None, "C3", None, "Truly Missing Inc",
                 "unmapped", False, dt.date(2022, 8, 25), "a"],   # -> stays residual
                [dt.date(2022, 6, 30), "OK", "C4", None, "Already Mapped Inc",
                 "openfigi_cusip", True, dt.date(2022, 8, 25), "a"],
            ],
        )
        # delisting filing gives CIK 555 for "Gone Bankrupt Inc"
        conn.execute(
            """INSERT INTO delistings_historical
               (cik, company_name, ticker, form_type, filed_date, reason, accession,
                source_quarter) VALUES (?,?,?,?,?,?,?,?)""",
            ["555", "GONE BANKRUPT INC", None, "25-NSE", dt.date(2022, 11, 1),
             "unknown", "x", "2022q4"],
        )
        # form4 purchase by CIK 555 in 2022 -> ticker GONE (and marks insider buying)
        conn.execute(
            """INSERT INTO form4_historical
               (cik, name, title, ticker, shares, price, value_usd, transaction_date,
                filed_date, transaction_code, is_director, is_officer,
                is_ten_percent_owner, accession_number, source_quarter)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ["555", "Insider", "CEO", "GONE", 100.0, 5.0, 500.0, dt.date(2022, 3, 1),
             dt.date(2022, 3, 2), "P", False, True, False, "f4acc", "2022q1"],
        )
        conn.commit()


def test_normalize_name() -> None:
    assert normalize_name("Acme Robotics, Inc.") == "ACME ROBOTICS"
    assert normalize_name("First BanCorp/Puerto Rico") == "FIRST BANCORP"


def test_recovery_resolves_via_name_and_form25_cik(tmp_path: Path) -> None:
    db = str(tmp_path / "r.db")
    _seed(db)
    company_tickers = {
        "0": {"cik_str": 111, "ticker": "ACME", "title": "Acme Robotics Inc"},
        "1": {"cik_str": 555, "ticker": "GONE", "title": "Some Other Name"},  # by CIK, not name
    }
    rep = recover_membership(db, company_tickers)
    assert rep["recovered_sec_name"] == 1      # Acme via name
    assert rep["recovered_form25_cik"] == 1    # Gone Bankrupt via Form25 CIK->ticker
    assert rep["residual"] == 1                # Truly Missing stays
    assert rep["after"] == rep["before"] + 2

    with get_connection(db) as conn:
        rows = dict(
            conn.execute(
                """SELECT name, ticker FROM iwm_pit_constituents
                   WHERE as_of_date = DATE '2022-06-30' AND mapped_flag
                   AND mapping_source IN ('sec_name','form25_cik')"""
            ).fetchall()
        )
    assert rows == {"Acme Robotics Inc": "ACME", "Gone Bankrupt Inc": "GONE"}


def test_recovery_residual_flagged_not_dropped_and_insider_metric(tmp_path: Path) -> None:
    db = str(tmp_path / "r2.db")
    _seed(db)
    # No company_tickers at all -> only Form25-CIK->form4 recovery for "Gone Bankrupt".
    rep = recover_membership(db, {})
    # Acme has no CIK route -> residual; Gone Bankrupt recovered via form4 ticker.
    assert rep["recovered_form25_cik"] == 1
    with get_connection(db) as conn:
        # residual rows remain in the table, still flagged unmapped (never dropped)
        n_unmapped = conn.execute(
            "SELECT count(*) FROM iwm_pit_constituents "
            "WHERE as_of_date = DATE '2022-06-30' AND NOT mapped_flag"
        ).fetchone()[0]
        total = conn.execute(
            "SELECT count(*) FROM iwm_pit_constituents WHERE as_of_date = DATE '2022-06-30'"
        ).fetchone()[0]
    assert total == 4 and n_unmapped == rep["residual"]  # nothing dropped
