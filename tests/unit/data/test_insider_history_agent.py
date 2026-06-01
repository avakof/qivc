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
    assert agent.source_name == "sec_edgar_insider_history"

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
