"""
Integration of the CMP path after the OQ-2 fix: InsiderHistoryAgent (which now
measures years_of_history from the earliest fetched P-code filing) feeding the
Cohen-Malloy-Pomorski classifier.

Confirms the strict CMP semantics: classifiability is about TRADING history,
not employment tenure. A first-time buyer (no prior open-market activity) is
UNCLASSIFIED; a repeat buyer trading across ≥3 prior years is classifiable.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pandas as pd

from qivc.data.insider_history_agent import InsiderHistoryAgent
from qivc.filters.insider_classifier import classify
from qivc.schemas import InsiderTransaction


class _Collection:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, i: int) -> Any:
        return self._items[i]


def _filing(filed: str, cik: str) -> Any:
    """An edgartools-shaped Form 4 filing with a single P-code purchase on `filed`."""
    df = pd.DataFrame(
        [
            {
                "Code": "P",
                "Date": filed[:10],
                "Shares": 1000.0,
                "Price": 50.0,
                "AcquiredDisposed": "A",
                "Security": "Common Stock",
                "Remaining": 5000.0,
            }
        ]
    )
    nd_txns = SimpleNamespace(empty=False, data=df)
    nd_table = SimpleNamespace(empty=False, transactions=nd_txns)
    issuer = SimpleNamespace(ticker="FCN", name="Co", cik="000")
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
    f = SimpleNamespace(filed=filed)
    f.obj = lambda: ownership  # type: ignore[assignment]
    return f


def _candidate(cik: str, d: date) -> InsiderTransaction:
    return InsiderTransaction(
        cik=cik,
        name="Insider",
        title="CFO",
        ticker="FCN",
        shares=1000.0,
        price=50.0,
        value_usd=50_000.0,
        transaction_date=d,
        filed_date=d,
        transaction_code="P",
        is_director=False,
        is_officer=True,
        is_ten_percent_owner=False,
    )


async def test_first_time_buyer_is_unclassified() -> None:
    """
    A first-time buyer — only the current purchase, no prior open-market activity —
    has years_of_history = 0 and is UNCLASSIFIED, regardless of seniority.
    """
    today = date.today()
    # History contains only a single, just-filed purchase → 0 years of trading history.
    collection = _Collection([_filing(today.isoformat(), cik="FIRST")])
    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)
    history = await agent.fetch(cik="FIRST", years=3)

    assert history.years_of_history == 0
    candidate = _candidate("FIRST", today)
    assert classify(candidate, history) == "unclassified"


async def test_repeat_buyer_three_years_apart_is_classified() -> None:
    """
    A repeat buyer with purchases in each of the prior 3-4 years has
    years_of_history >= 3 and is therefore CLASSIFIABLE (opportunistic or
    routine, depending on the calendar-month pattern) — not unclassified.
    """
    today = date.today()
    y, m = today.year, today.month
    # Same-month purchases in each of the prior four years → classifiable.
    priors = [
        _filing(date(y - 1, m, 15).isoformat(), cik="REPEAT"),
        _filing(date(y - 2, m, 15).isoformat(), cik="REPEAT"),
        _filing(date(y - 3, m, 15).isoformat(), cik="REPEAT"),
        _filing(date(y - 4, m, 15).isoformat(), cik="REPEAT"),
    ]
    collection = _Collection(priors)
    client = mock.AsyncMock()
    client.get_insider_history = mock.AsyncMock(return_value=collection)

    agent = InsiderHistoryAgent(client=client)
    history = await agent.fetch(cik="REPEAT", years=3)

    assert history.years_of_history >= 3
    candidate = _candidate("REPEAT", date(y, m, 15))
    result = classify(candidate, history)
    assert result in ("opportunistic", "routine")
    # Same-month buys in each of the 3 prior years → routine per CMP.
    assert result == "routine"
