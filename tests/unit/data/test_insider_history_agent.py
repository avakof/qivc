"""
Unit tests for InsiderHistoryAgent.

`years_of_history` is computed from the EARLIEST filed date in the insider's
fetched Form 4 history (OQ-2 fix): floor((today - earliest_filed) / 365.25),
or 0 when there are no prior filings. Fixtures use dates relative to today so
the assertions are deterministic regardless of the calendar date the suite runs.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pandas as pd
import pytest

from qivc.data.insider_history_agent import InsiderHistoryAgent
from qivc.schemas import InsiderHistory

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


class _Collection:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, i: int) -> Any:
        return self._items[i]


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _make_filing_with_p(
    filed: str,
    cik: str,
    ticker: str,
    shares: float = 1000.0,
    price: float = 50.0,
) -> Any:
    df = pd.DataFrame(
        [
            {
                "Code": "P",
                "Date": filed[:10],
                "Shares": shares,
                "Price": price,
                "AcquiredDisposed": "A",
                "Security": "Common Stock",
                "Remaining": 5000.0,
            }
        ]
    )
    nd_txns = SimpleNamespace(empty=False, data=df)
    nd_table = SimpleNamespace(empty=False, transactions=nd_txns)
    issuer = SimpleNamespace(ticker=ticker, name="Company", cik="000")
    owner = SimpleNamespace(
        cik=cik,
        name="Insider",
        officer_title="CFO",
        is_director=False,
        is_officer=True,
        is_ten_pct_owner=False,
        is_company=False,
        position="CFO",
    )
    ro = SimpleNamespace(owners=[owner])
    ownership = SimpleNamespace(issuer=issuer, reporting_owners=ro, non_derivative_table=nd_table)
    filing = SimpleNamespace(filed=filed)
    filing.obj = lambda: ownership  # type: ignore[assignment]
    return filing


async def test_insider_history_returns_correct_cik() -> None:
    # Earliest filing ~3 years + 1 month ago → years_of_history == 3.
    filings = [_make_filing_with_p(_days_ago(int(3 * 365.25) + 30), cik="99999", ticker="FCN")]
    collection = _Collection(filings)

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)

    result: InsiderHistory = await agent.fetch(cik="99999", years=3)

    assert result.cik == "99999"
    assert result.years_of_history == 3
    assert len(result.transactions) == 1
    assert result.transactions[0].cik == "99999"


async def test_insider_history_multiple_filings_uses_earliest() -> None:
    # Two filings; earliest is ~3.5 years old → years_of_history >= 3.
    filings = [
        _make_filing_with_p(_days_ago(400), cik="12345", ticker="FCN", shares=500.0, price=80.0),
        _make_filing_with_p(
            _days_ago(int(3 * 365.25) + 90), cik="12345", ticker="FCN", shares=1500.0, price=70.0
        ),
    ]
    collection = _Collection(filings)

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)

    result = await agent.fetch(cik="12345", years=3)
    assert len(result.transactions) == 2
    assert result.years_of_history >= 3  # driven by the OLDEST filing


async def test_insider_history_empty_filings_is_zero_years() -> None:
    collection = _Collection([])

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)
    assert agent.source_name == "sec_insider_history_bulk+live"

    result = await agent.fetch(cik="00000", years=3)
    assert result.transactions == []
    assert result.years_of_history == 0  # no filings → no measured history


async def test_years_of_history_six_months_returns_zero() -> None:
    """An insider with only ~6 months of Form 4 history → years_of_history == 0."""
    filings = [_make_filing_with_p(_days_ago(183), cik="55555", ticker="FCN")]
    collection = _Collection(filings)

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)
    result = await agent.fetch(cik="55555", years=3)
    assert result.years_of_history == 0


async def test_years_of_history_four_years_returns_four() -> None:
    """An insider whose earliest filing is 4+ years old → years_of_history >= 4."""
    filings = [
        _make_filing_with_p(_days_ago(int(4 * 365.25) + 15), cik="44444", ticker="FCN"),
        _make_filing_with_p(_days_ago(200), cik="44444", ticker="FCN"),  # a recent one too
    ]
    collection = _Collection(filings)

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)
    result = await agent.fetch(cik="44444", years=3)
    assert result.years_of_history >= 4


async def test_insider_history_client_error_raises() -> None:
    from qivc.exceptions import QivcDataError

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(side_effect=Exception("network"))
    agent = InsiderHistoryAgent(client=client)

    with pytest.raises(QivcDataError):
        await agent.fetch(cik="11111", years=3)


async def test_insider_history_skips_none_ownership() -> None:
    """Filing where obj() returns None should be skipped → no measured history."""
    filing = SimpleNamespace(filed=_days_ago(400))
    filing.obj = lambda: None  # type: ignore[assignment]
    collection = _Collection([filing])

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)
    agent = InsiderHistoryAgent(client=client)

    result = await agent.fetch(cik="22222", years=3)
    assert result.transactions == []
    assert result.years_of_history == 0


async def test_insider_history_skips_bad_filing_obj() -> None:
    """Filing where obj() raises should be skipped → no measured history."""
    filing = SimpleNamespace(filed=_days_ago(400))
    filing.obj = mock.MagicMock(side_effect=Exception("parse error"))  # type: ignore[assignment]
    collection = _Collection([filing])

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)
    agent = InsiderHistoryAgent(client=client)

    result = await agent.fetch(cik="33333", years=3)
    assert result.transactions == []
    assert result.years_of_history == 0


# ---------------------------------------------------------------------------
# Bulk-first history (Task 8.3 part 1)
# ---------------------------------------------------------------------------

from datetime import datetime as _datetime  # noqa: E402

from qivc.storage.db import get_connection as _get_conn  # noqa: E402


def _insert_hist_row(conn: Any, cik: str, filed: date, ticker: str = "AAA") -> None:
    conn.execute(
        """
        INSERT INTO form4_historical (
            cik, name, title, ticker, shares, price, value_usd,
            transaction_date, filed_date, transaction_code,
            is_director, is_officer, is_ten_percent_owner,
            accession_number, source_quarter
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            cik,
            f"Insider {cik}",
            "CFO",
            ticker,
            1000.0,
            50.0,
            50_000.0,
            filed,
            filed,
            "P",
            False,
            True,
            False,
            f"ACC-{cik}-{filed}",
            "2024q1",
        ],
    )


def _bulk_store(tmp_path: Path, rows: list[tuple[str, date]], *, cover: bool = True) -> str:
    """Create a form4_historical store. If cover=True, add an anchor row old
    enough that the store reaches back past the 3-year CMP window."""
    db = str(tmp_path / "qivc.db")
    now = _datetime(2026, 5, 1)
    with _get_conn(db) as conn:
        conn.execute(
            "INSERT INTO bulk_load_progress VALUES (?,?,?,?)",
            ["2026q1", now, 0, "complete"],
        )
        if cover:
            # Anchor well before today-3yr so earliest_filed_date covers the window.
            _insert_hist_row(conn, "999999", date.today() - timedelta(days=1300), "ZZZ")
        for cik, filed in rows:
            _insert_hist_row(conn, cik, filed)
        conn.commit()
    return db


async def test_insider_history_uses_bulk_when_available(tmp_path: Path) -> None:
    """A CIK present in a covering bulk store is read locally; live is NOT called."""
    db = _bulk_store(
        tmp_path,
        [("111", date.today() - timedelta(days=400)), ("111", date.today() - timedelta(days=100))],
    )
    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock()
    agent = InsiderHistoryAgent(client=client, db_path=db)

    result = await agent.fetch(cik="111", years=3)

    assert len(result.transactions) == 2
    assert all(t.cik == "111" for t in result.transactions)
    client.get_insider_history.assert_not_called()  # served from the bulk store


async def test_insider_history_bulk_satisfies_3yr_window(tmp_path: Path) -> None:
    """A CIK whose earliest in-window filing is ~3yr old yields years_of_history>=3."""
    window_start = date.today() - timedelta(days=round(3 * 365.25))
    db = _bulk_store(
        tmp_path,
        [
            ("222", window_start),  # exactly at the window edge → ~3 years deep
            ("222", date.today() - timedelta(days=200)),
        ],
    )
    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock()
    agent = InsiderHistoryAgent(client=client, db_path=db)

    result = await agent.fetch(cik="222", years=3)

    assert result.years_of_history >= 3
    client.get_insider_history.assert_not_called()


async def test_insider_history_falls_back_to_live_for_unknown_cik(tmp_path: Path) -> None:
    """When the bulk store cannot cover the window, fall back to live EDGAR."""
    # Shallow store: only recent data, so it does NOT reach back 3 years.
    db = _bulk_store(tmp_path, [("333", date.today() - timedelta(days=30))], cover=False)

    live_filing = _make_filing_with_p(_days_ago(100), "333", "AAA")
    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=_Collection([live_filing]))
    agent = InsiderHistoryAgent(client=client, db_path=db)

    result = await agent.fetch(cik="333", years=3)

    client.get_insider_history.assert_called_once()  # bulk could not cover → live
    assert len(result.transactions) == 1


async def test_insider_history_absent_cik_in_covered_store_skips_live(tmp_path: Path) -> None:
    """
    Speed-critical: a CIK absent from a store that DOES cover the window is
    definitively unclassified (no 3-prior-year history) — return empty WITHOUT a
    live call. This is why a full screen's history step drops to seconds.
    """
    db = _bulk_store(tmp_path, [("111", date.today() - timedelta(days=100))])
    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock()
    agent = InsiderHistoryAgent(client=client, db_path=db)

    result = await agent.fetch(cik="404404", years=3)  # CIK not in the store

    assert result.transactions == []
    assert result.years_of_history == 0
    client.get_insider_history.assert_not_called()  # no pointless network call


async def test_insider_history_per_run_cache(tmp_path: Path) -> None:
    """A CIK is read once per run; the second fetch is served from cache."""
    db = _bulk_store(tmp_path, [("111", date.today() - timedelta(days=100))])
    client = mock.AsyncMock()
    agent = InsiderHistoryAgent(client=client, db_path=db)

    first = await agent.fetch(cik="111", years=3)
    second = await agent.fetch(cik="111", years=3)
    assert first is second  # identical cached object
