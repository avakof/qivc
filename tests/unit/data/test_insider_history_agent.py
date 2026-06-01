"""Unit tests for InsiderHistoryAgent — 3-year window correctness."""

from __future__ import annotations

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
    filings = [_make_filing_with_p("2024-05-01", cik="99999", ticker="FCN")]
    collection = _Collection(filings)

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)

    result: InsiderHistory = await agent.fetch(cik="99999", years=3)

    assert result.cik == "99999"
    assert result.years_of_history == 3
    assert len(result.transactions) == 1
    assert result.transactions[0].cik == "99999"


async def test_insider_history_multiple_filings() -> None:
    filings = [
        _make_filing_with_p("2024-01-10", cik="12345", ticker="FCN", shares=500.0, price=80.0),
        _make_filing_with_p("2023-06-15", cik="12345", ticker="FCN", shares=1500.0, price=70.0),
    ]
    collection = _Collection(filings)

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)

    result = await agent.fetch(cik="12345", years=3)
    assert len(result.transactions) == 2


async def test_insider_history_empty_filings() -> None:
    collection = _Collection([])

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)
    assert agent.source_name == "sec_edgar_insider_history"

    result = await agent.fetch(cik="00000", years=3)
    assert result.transactions == []
    assert result.years_of_history == 3


async def test_insider_history_client_error_raises() -> None:
    from qivc.exceptions import QivcDataError

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(side_effect=Exception("network"))
    agent = InsiderHistoryAgent(client=client)

    with pytest.raises(QivcDataError):
        await agent.fetch(cik="11111", years=3)


async def test_insider_history_skips_none_ownership() -> None:
    """Filing where obj() returns None should be skipped."""
    filing = SimpleNamespace(filed="2024-01-01")
    filing.obj = lambda: None  # type: ignore[assignment]
    collection = _Collection([filing])

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)
    agent = InsiderHistoryAgent(client=client)

    result = await agent.fetch(cik="22222", years=3)
    assert result.transactions == []


async def test_insider_history_skips_bad_filing_obj() -> None:
    """Filing where obj() raises should be skipped."""
    filing = SimpleNamespace(filed="2024-01-01")
    filing.obj = mock.MagicMock(side_effect=Exception("parse error"))  # type: ignore[assignment]
    collection = _Collection([filing])

    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)
    agent = InsiderHistoryAgent(client=client)

    result = await agent.fetch(cik="33333", years=3)
    assert result.transactions == []
