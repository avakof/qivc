"""
Live schema-equivalence check: a bulk-loaded record must match the edgartools
record for the same Form 4 filing (Task 8.1, clarification #3).

Marked `@pytest.mark.live` and SKIPPED by default (pyproject sets
`addopts = "-m 'not live'"`). Run manually with::

    uv run pytest -m live tests/live/test_bulk_equivalence.py

Uses a committed fixture (tests/fixtures/bulk_aflac/) carrying the three SEC
bulk TSV rows for AFLAC accession 0000004977-24-000017 — a clean single-owner,
single open-market-purchase Form 4 — and fetches the same filing live from
EDGAR via edgartools.

Fields that must be IDENTICAL: cik, ticker, shares, price, transaction_date,
transaction_code, is_director, is_officer, is_ten_percent_owner.

Documented benign differences (see BULK_DATA_NOTES.md), NOT asserted equal:
  - name:  bulk keeps SEC's raw "LAST FIRST" casing; edgartools normalises it.
            Insider identity is matched on CIK (identical), never name.
  - title: bulk uses the raw RPTOWNER_TITLE (blank for directors); edgartools
            synthesises "Director". The is_director flag is identical in both.
"""

from __future__ import annotations

from pathlib import Path

import edgar
import pytest

from qivc.config import Settings
from qivc.data.bulk_loader import import_quarter
from qivc.data.form4_agent import _parse_ownership
from qivc.storage.db import get_connection

pytestmark = pytest.mark.live

_ACCESSION = "0000004977-24-000017"
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "bulk_aflac"


def test_bulk_record_matches_edgartools_record(tmp_path: Path) -> None:
    settings = Settings()
    edgar.set_identity(settings.edgar_user_agent)

    # --- bulk side: import the fixture rows and read back the record ---
    db = str(tmp_path / "q.db")
    with get_connection(db) as conn:
        import_quarter(
            conn,
            _FIXTURE / "SUBMISSION.tsv",
            _FIXTURE / "REPORTINGOWNER.tsv",
            _FIXTURE / "NONDERIV_TRANS.tsv",
            "2024q1",
        )
        rows = conn.execute(
            """
            SELECT cik, ticker, shares, price, transaction_date, transaction_code,
                   is_director, is_officer, is_ten_percent_owner
            FROM form4_historical
            """
        ).fetchall()
    assert len(rows) == 1, "fixture should yield exactly one bulk record"
    bulk = rows[0]

    # --- edgartools side: fetch the same filing live and parse its P txns ---
    filing = edgar.find(_ACCESSION)
    txns = _parse_ownership(filing, filing.obj())
    assert len(txns) == 1, "edgartools should yield exactly one P transaction"
    edg = txns[0]

    # CIK (zero-padded both sides), ticker, economics, dates, code, flags.
    assert bulk[0].lstrip("0") == edg.cik.lstrip("0")
    assert bulk[1] == edg.ticker
    assert bulk[2] == edg.shares
    assert bulk[3] == edg.price
    assert bulk[4] == edg.transaction_date
    assert bulk[5] == edg.transaction_code
    assert bulk[6] == edg.is_director
    assert bulk[7] == edg.is_officer
    assert bulk[8] == edg.is_ten_percent_owner
