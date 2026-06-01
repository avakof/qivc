"""
Unit tests for point-in-time data logic — the no-look-ahead guarantee.

These verify the PURE date-filtering function `filter_by_filing_date`, which is
the core of PIT correctness: no transaction filed on or after the decision date
may ever appear in the as-of set.
"""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from qivc.backtest.pit import (
    SECTOR_MEDIAN_LOOKAHEAD_NOTE,
    filter_by_filing_date,
    get_sector_median_as_of,
)
from qivc.schemas import InsiderTransaction


def _txn(filed: date, tx: date | None = None) -> InsiderTransaction:
    return InsiderTransaction(
        cik="1",
        name="Insider",
        title="CEO",
        ticker="UNH",
        shares=100.0,
        price=10.0,
        value_usd=1000.0,
        transaction_date=tx or filed,
        filed_date=filed,
        transaction_code="P",
        is_director=False,
        is_officer=True,
        is_ten_percent_owner=False,
    )


def test_excludes_filing_on_or_after_as_of() -> None:
    as_of = date(2026, 5, 15)
    txns = [
        _txn(date(2026, 5, 14)),  # before → kept
        _txn(date(2026, 5, 15)),  # exactly as_of → excluded (strict <)
        _txn(date(2026, 5, 16)),  # after → excluded
    ]
    result = filter_by_filing_date(txns, as_of)
    assert len(result) == 1
    assert result[0].filed_date == date(2026, 5, 14)


def test_empty_input() -> None:
    assert filter_by_filing_date([], date(2026, 1, 1)) == []


def test_all_before_kept() -> None:
    as_of = date(2026, 6, 1)
    txns = [_txn(date(2026, 5, d)) for d in (1, 10, 20, 31)]
    assert len(filter_by_filing_date(txns, as_of)) == 4


def test_transaction_date_irrelevant_only_filing_date_matters() -> None:
    """A trade transacted long ago but FILED after as_of must be excluded."""
    as_of = date(2026, 5, 15)
    # transaction happened in April, but the Form 4 was filed May 16 (late filing)
    late_filed = _txn(filed=date(2026, 5, 16), tx=date(2026, 4, 1))
    assert filter_by_filing_date([late_filed], as_of) == []


@given(
    filed_offsets=st.lists(st.integers(min_value=-30, max_value=30), max_size=40),
)
def test_property_no_future_leak(filed_offsets: list[int]) -> None:
    """For any set of filings, none in the result is filed on/after as_of."""
    as_of = date(2026, 5, 15)
    from datetime import timedelta

    txns = [_txn(as_of + timedelta(days=off)) for off in filed_offsets]
    result = filter_by_filing_date(txns, as_of)
    assert all(t.filed_date < as_of for t in result)
    # and every qualifying input is retained
    expected = sum(1 for off in filed_offsets if off < 0)
    assert len(result) == expected


def test_sector_median_as_of_returns_none_with_documented_note() -> None:
    assert get_sector_median_as_of("Health Care", date(2024, 1, 1)) is None
    assert "look-ahead bias" in SECTOR_MEDIAN_LOOKAHEAD_NOTE


# ---------------------------------------------------------------------------
# get_form4_as_of (async) — PIT filtering over a mocked client
# ---------------------------------------------------------------------------


class _FakeFilings:
    def __init__(self, items: list) -> None:
        self._items = items

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, i: int) -> object:
        return self._items[i]


def _fake_form4_filing(filed: date, cik: str) -> object:
    """A SimpleNamespace mimicking an edgartools Form 4 filing with one P-trade."""
    from types import SimpleNamespace

    import pandas as pd

    df = pd.DataFrame(
        [
            {
                "Code": "P",
                "Date": filed.isoformat(),
                "Shares": 100.0,
                "Price": 10.0,
                "AcquiredDisposed": "A",
                "Security": "Common",
                "Remaining": 0.0,
            }
        ]
    )
    nd_txns = SimpleNamespace(empty=False, data=df)
    nd_table = SimpleNamespace(empty=False, transactions=nd_txns)
    owner = SimpleNamespace(
        cik=cik,
        name="Insider",
        officer_title="CEO",
        is_director=False,
        is_officer=True,
        is_ten_pct_owner=False,
        is_company=False,
        position="CEO",
    )
    ownership = SimpleNamespace(
        issuer=SimpleNamespace(ticker="UNH", name="UNH", cik="000"),
        reporting_owners=SimpleNamespace(owners=[owner]),
        non_derivative_table=nd_table,
    )
    filing = SimpleNamespace(filed=filed.isoformat())
    filing.obj = lambda: ownership  # type: ignore[attr-defined]
    return filing


async def test_get_form4_as_of_excludes_future_filings() -> None:
    from datetime import timedelta
    from unittest import mock

    from qivc.backtest.pit import get_form4_as_of

    as_of = date(2026, 5, 15)
    # The agent's _parse_ownership uses the transaction Date as filed_date when the
    # filing.filed parses; here both are the same date for simplicity.
    filings = _FakeFilings(
        [
            _fake_form4_filing(as_of - timedelta(days=2), "A"),  # before → kept
            _fake_form4_filing(as_of, "B"),  # on as_of → excluded
            _fake_form4_filing(as_of + timedelta(days=1), "C"),  # after → excluded
        ]
    )
    client = mock.AsyncMock()
    client.get_form4_filings = mock.AsyncMock(return_value=filings)

    result = await get_form4_as_of(client, as_of, lookback_days=14)
    assert all(t.filed_date < as_of for t in result)
    assert {t.cik for t in result} == {"A"}


async def test_get_form4_as_of_none_filings() -> None:
    from unittest import mock

    from qivc.backtest.pit import get_form4_as_of

    client = mock.AsyncMock()
    client.get_form4_filings = mock.AsyncMock(return_value=None)
    assert await get_form4_as_of(client, date(2026, 5, 15)) == []


def test_filing_date_helper() -> None:
    from types import SimpleNamespace

    from qivc.backtest.pit import _filing_date

    assert _filing_date(SimpleNamespace(filing_date="2026-05-01")) == date(2026, 5, 1)
    assert _filing_date(SimpleNamespace(filed="2026-05-02")) == date(2026, 5, 2)
    assert _filing_date(SimpleNamespace(filing_date=date(2026, 5, 3))) == date(2026, 5, 3)
    assert _filing_date(SimpleNamespace(filing_date=None, filed=None)) is None
    assert _filing_date(SimpleNamespace(filing_date="garbage")) is None
