"""Unit tests for Form4Agent — fixture parsing and P-code filtering."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pandas as pd
import pytest

from qivc.data.form4_agent import Form4Agent, _parse_ownership
from qivc.schemas import InsiderTransaction

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


class _Collection:
    """Minimal indexable collection with proper __len__ for agent compatibility."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, i: int) -> Any:
        return self._items[i]


# ---------------------------------------------------------------------------
# helpers to build mock edgartools objects from fixture JSON
# ---------------------------------------------------------------------------


def _make_filing(filed_str: str, ownership_ns: Any) -> Any:
    filing = SimpleNamespace(filed=filed_str)
    filing.obj = lambda: ownership_ns  # type: ignore[assignment]
    return filing


def _make_ownership(record: dict[str, Any]) -> Any:
    """Build a SimpleNamespace that mimics the edgartools Ownership structure."""
    owners = []
    for o in record.get("reporting_owners", []):
        owners.append(
            SimpleNamespace(
                cik=o["cik"],
                name=o["name"],
                officer_title=o.get("officer_title", ""),
                is_director=o.get("is_director", False),
                is_officer=o.get("is_officer", False),
                is_ten_pct_owner=o.get("is_ten_pct_owner", False),
                is_company=o.get("is_company", False),
                position=o.get("officer_title", ""),
            )
        )

    txns = record.get("non_derivative_transactions", [])
    if txns:
        df = pd.DataFrame(txns)
        nd_txns_ns = SimpleNamespace(empty=False, data=df)
    else:
        nd_txns_ns = SimpleNamespace(empty=True, data=pd.DataFrame())

    nd_table = SimpleNamespace(
        empty=(len(txns) == 0),
        transactions=nd_txns_ns,
    )

    issuer_data = record.get("issuer", {})
    issuer = SimpleNamespace(
        ticker=issuer_data.get("ticker", ""),
        name=issuer_data.get("name", ""),
        cik=issuer_data.get("cik", ""),
    )

    reporting_owners_ns = SimpleNamespace(owners=owners)

    return SimpleNamespace(
        issuer=issuer,
        reporting_owners=reporting_owners_ns,
        non_derivative_table=nd_table,
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_parse_ownership_filters_to_p_code() -> None:
    """Only P-code transactions should be returned."""
    fcn_data: list[dict[str, Any]] = json.loads((FIXTURES / "form4_fcn.json").read_text())
    # Find a record that has P-code transactions
    p_record = next(
        r for r in fcn_data if any(t["Code"] == "P" for t in r["non_derivative_transactions"])
    )
    filing = _make_filing(p_record["filed"], _make_ownership(p_record))
    ownership = filing.obj()

    txns = _parse_ownership(filing, ownership)

    assert all(t.transaction_code == "P" for t in txns)
    assert len(txns) > 0


def test_parse_ownership_non_p_codes_excluded() -> None:
    """Transactions with codes other than P must not appear."""
    record: dict[str, Any] = {
        "filed": "2026-05-01",
        "issuer": {"ticker": "TEST", "name": "Test Corp", "cik": "999"},
        "reporting_owners": [
            {
                "cik": "12345",
                "name": "John Doe",
                "officer_title": "CFO",
                "is_officer": True,
                "is_director": False,
                "is_ten_pct_owner": False,
                "is_company": False,
            }
        ],
        "non_derivative_transactions": [
            {
                "Code": "S",
                "Date": "2026-04-30",
                "Shares": 1000.0,
                "Price": 50.0,
                "AcquiredDisposed": "D",
                "Security": "Common Stock",
                "Remaining": 5000.0,
            },
            {
                "Code": "A",
                "Date": "2026-04-29",
                "Shares": 500.0,
                "Price": 0.0,
                "AcquiredDisposed": "A",
                "Security": "Common Stock",
                "Remaining": 6000.0,
            },
        ],
    }
    filing = _make_filing(record["filed"], _make_ownership(record))
    txns = _parse_ownership(filing, filing.obj())
    assert txns == []


def test_parse_ownership_value_usd_computed() -> None:
    """value_usd must equal shares * price."""
    record: dict[str, Any] = {
        "filed": "2026-05-10",
        "issuer": {"ticker": "DEMO", "name": "Demo Inc", "cik": "777"},
        "reporting_owners": [
            {
                "cik": "88888",
                "name": "Jane Smith",
                "officer_title": "CEO",
                "is_officer": True,
                "is_director": True,
                "is_ten_pct_owner": False,
                "is_company": False,
            }
        ],
        "non_derivative_transactions": [
            {
                "Code": "P",
                "Date": "2026-05-09",
                "Shares": 2000.0,
                "Price": 75.0,
                "AcquiredDisposed": "A",
                "Security": "Common Stock",
                "Remaining": 10000.0,
            },
        ],
    }
    filing = _make_filing(record["filed"], _make_ownership(record))
    txns = _parse_ownership(filing, filing.obj())

    assert len(txns) == 1
    t = txns[0]
    assert t.value_usd == pytest.approx(2000.0 * 75.0)
    assert t.ticker == "DEMO"
    assert t.name == "Jane Smith"
    assert t.is_officer is True
    assert t.transaction_date == date(2026, 5, 9)


async def test_form4_agent_fetch_returns_only_p_codes() -> None:
    """Form4Agent.fetch() must aggregate P-code transactions across filings."""
    fcn_data: list[dict[str, Any]] = json.loads((FIXTURES / "form4_fcn.json").read_text())

    mock_filings = [_make_filing(rec["filed"], _make_ownership(rec)) for rec in fcn_data]
    mock_collection = _Collection(mock_filings)

    client = mock.AsyncMock()
    client.get_form4_filings = mock.AsyncMock(return_value=mock_collection)

    agent = Form4Agent(client=client)
    result = await agent.fetch(ticker="FCN", lookback_days=60)

    assert all(isinstance(t, InsiderTransaction) for t in result)
    assert all(t.transaction_code == "P" for t in result)


async def test_form4_agent_empty_filings() -> None:
    """Empty filing list should produce empty result, not an error."""
    mock_collection = _Collection([])

    client = mock.AsyncMock()
    client.get_form4_filings = mock.AsyncMock(return_value=mock_collection)

    agent = object.__new__(Form4Agent)
    agent._client = client  # type: ignore[attr-defined]

    result = await agent.fetch(ticker="XYZ", lookback_days=14)
    assert result == []


# ---------------------------------------------------------------------------
# Form4Agent __init__ and source_name
# ---------------------------------------------------------------------------


def test_form4_agent_init_and_source_name() -> None:
    client = mock.AsyncMock()
    agent = Form4Agent(client=client)
    assert agent.source_name == "sec_edgar_form4"
    assert agent._client is client  # type: ignore[attr-defined]


async def test_form4_agent_fetch_raises_on_client_error() -> None:
    """If client.get_form4_filings raises, Form4Agent should re-raise as QivcDataError."""
    from qivc.exceptions import QivcDataError

    client = mock.AsyncMock()
    client.get_form4_filings = mock.AsyncMock(side_effect=Exception("network down"))
    agent = Form4Agent(client=client)

    with pytest.raises(QivcDataError):
        await agent.fetch(ticker="FCN")


async def test_form4_agent_fetch_none_filings_returns_empty() -> None:
    client = mock.AsyncMock()
    client.get_form4_filings = mock.AsyncMock(return_value=None)
    agent = Form4Agent(client=client)

    result = await agent.fetch(ticker="FCN")
    assert result == []


async def test_form4_agent_skips_bad_filing_obj() -> None:
    """If filing.obj() raises, the filing should be skipped."""

    class FakeFilings:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, i: int) -> Any:
            f = SimpleNamespace(filed="2026-01-01")
            f.obj = mock.MagicMock(side_effect=Exception("parse error"))  # type: ignore[assignment]
            return f

    client = mock.AsyncMock()
    client.get_form4_filings = mock.AsyncMock(return_value=FakeFilings())
    agent = Form4Agent(client=client)

    result = await agent.fetch(ticker="FCN")
    assert result == []


async def test_form4_agent_skips_none_ownership() -> None:
    """If filing.obj() returns None, that filing should be skipped (continue)."""

    class FakeFilings:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, i: int) -> Any:
            f = SimpleNamespace(filed="2026-01-01")
            f.obj = lambda: None  # type: ignore[assignment]
            return f

    client = mock.AsyncMock()
    client.get_form4_filings = mock.AsyncMock(return_value=FakeFilings())
    agent = Form4Agent(client=client)

    result = await agent.fetch(ticker="FCN")
    assert result == []


def test_parse_ownership_empty_reporting_owners() -> None:
    """An Ownership with no reporting_owners must return empty list."""
    ownership = SimpleNamespace(
        issuer=SimpleNamespace(ticker="FCN", name="FCN Corp", cik="123"),
        reporting_owners=SimpleNamespace(owners=[]),
        non_derivative_table=None,
    )
    filing = SimpleNamespace(filed="2026-01-01")
    assert _parse_ownership(filing, ownership) == []


def test_parse_ownership_none_nd_table_returns_empty() -> None:
    """nd_table=None should return empty without error."""
    owner = SimpleNamespace(
        cik="12345",
        name="Jane",
        officer_title="CEO",
        is_director=True,
        is_officer=True,
        is_ten_pct_owner=False,
        is_company=False,
        position="CEO",
    )
    ownership = SimpleNamespace(
        issuer=SimpleNamespace(ticker="FCN", name="FCN Corp", cik="123"),
        reporting_owners=SimpleNamespace(owners=[owner]),
        non_derivative_table=None,
    )
    filing = SimpleNamespace(filed="2026-01-01")
    assert _parse_ownership(filing, ownership) == []


def test_parse_ownership_empty_nd_table_returns_empty() -> None:
    """nd_table.empty=True should return empty."""
    owner = SimpleNamespace(
        cik="12345",
        name="Jane",
        officer_title="CEO",
        is_director=True,
        is_officer=True,
        is_ten_pct_owner=False,
        is_company=False,
        position="CEO",
    )
    ownership = SimpleNamespace(
        issuer=SimpleNamespace(ticker="FCN", name="FCN Corp", cik="123"),
        reporting_owners=SimpleNamespace(owners=[owner]),
        non_derivative_table=SimpleNamespace(empty=True, transactions=SimpleNamespace(empty=True)),
    )
    filing = SimpleNamespace(filed="2026-01-01")
    assert _parse_ownership(filing, ownership) == []


def test_parse_ownership_none_nd_txns_returns_empty() -> None:
    """nd_table.transactions=None should return empty."""
    owner = SimpleNamespace(
        cik="12345",
        name="Jane",
        officer_title="CEO",
        is_director=True,
        is_officer=True,
        is_ten_pct_owner=False,
        is_company=False,
        position="CEO",
    )
    ownership = SimpleNamespace(
        issuer=SimpleNamespace(ticker="FCN", name="FCN Corp", cik="123"),
        reporting_owners=SimpleNamespace(owners=[owner]),
        non_derivative_table=SimpleNamespace(empty=False, transactions=None),
    )
    filing = SimpleNamespace(filed="2026-01-01")
    assert _parse_ownership(filing, ownership) == []


def test_parse_ownership_bad_date_defaults_today() -> None:
    """Invalid transaction date should default to today without raising."""
    from datetime import date

    owner = SimpleNamespace(
        cik="12345",
        name="Jane",
        officer_title="CEO",
        is_director=True,
        is_officer=True,
        is_ten_pct_owner=False,
        is_company=False,
        position="CEO",
    )
    df = pd.DataFrame(
        [
            {
                "Code": "P",
                "Date": "not-a-date",
                "Shares": 100.0,
                "Price": 10.0,
                "AcquiredDisposed": "A",
                "Security": "Common",
                "Remaining": 500.0,
            }
        ]
    )
    nd_txns = SimpleNamespace(empty=False, data=df)
    nd_table = SimpleNamespace(empty=False, transactions=nd_txns)
    ownership = SimpleNamespace(
        issuer=SimpleNamespace(ticker="FCN", name="FCN Corp", cik="123"),
        reporting_owners=SimpleNamespace(owners=[owner]),
        non_derivative_table=nd_table,
    )
    filing = SimpleNamespace(filed="also-bad-date")
    txns = _parse_ownership(filing, ownership)
    assert len(txns) == 1
    assert txns[0].transaction_date == date.today()
    assert txns[0].filed_date == date.today()


def test_parse_ownership_non_numeric_shares_defaults_zero() -> None:
    """Non-numeric string Shares/Price triggers ValueError → defaults to 0.0."""
    owner = SimpleNamespace(
        cik="12345",
        name="Jane",
        officer_title="CEO",
        is_director=True,
        is_officer=True,
        is_ten_pct_owner=False,
        is_company=False,
        position="CEO",
    )

    # Use an object that evaluates as truthy but fails float() conversion
    class _Bad:
        def __bool__(self) -> bool:
            return True

        def __float__(self) -> float:
            raise ValueError("bad value")

    df = pd.DataFrame(
        [
            {
                "Code": "P",
                "Date": "2026-01-01",
                "Shares": "not-a-number",
                "Price": "also-bad",
                "AcquiredDisposed": "A",
                "Security": "Common",
                "Remaining": 0.0,
            }
        ]
    )
    nd_txns = SimpleNamespace(empty=False, data=df)
    nd_table = SimpleNamespace(empty=False, transactions=nd_txns)
    ownership = SimpleNamespace(
        issuer=SimpleNamespace(ticker="FCN", name="FCN Corp", cik="123"),
        reporting_owners=SimpleNamespace(owners=[owner]),
        non_derivative_table=nd_table,
    )
    filing = SimpleNamespace(filed="2026-01-01")
    txns = _parse_ownership(filing, ownership)
    assert len(txns) == 1
    assert txns[0].shares == pytest.approx(0.0)
    assert txns[0].price == pytest.approx(0.0)


def test_parse_ownership_data_read_exception() -> None:
    """If nd_table.transactions.data raises, return empty list gracefully."""
    owner = SimpleNamespace(
        cik="12345",
        name="Test",
        officer_title="CEO",
        is_director=True,
        is_officer=True,
        is_ten_pct_owner=False,
        is_company=False,
        position="CEO",
    )
    nd_txns = mock.MagicMock()
    nd_txns.empty = False
    nd_txns.data = mock.PropertyMock(side_effect=Exception("bad data"))
    type(nd_txns).data = mock.PropertyMock(side_effect=Exception("bad data"))

    nd_table = SimpleNamespace(empty=False, transactions=nd_txns)
    ownership = SimpleNamespace(
        issuer=SimpleNamespace(ticker="FCN", name="FCN Corp", cik="123"),
        reporting_owners=SimpleNamespace(owners=[owner]),
        non_derivative_table=nd_table,
    )
    filing = SimpleNamespace(filed="2026-01-01")
    # Should not raise — returns empty
    result = _parse_ownership(filing, ownership)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Import isolation check
# ---------------------------------------------------------------------------


def test_data_module_does_not_import_filters() -> None:
    """qivc.data must not import anything from qivc.filters at module level."""
    import sys

    data_modules = [name for name in sys.modules if name.startswith("qivc.data")]
    for mod_name in data_modules:
        mod = sys.modules[mod_name]
        src = getattr(mod, "__file__", None) or ""
        if not src:
            continue
        # Read the source and verify no top-level 'from qivc.filters' or 'import qivc.filters'
        try:
            text = Path(src).read_text()
            lines = [
                ln
                for ln in text.splitlines()
                if "qivc.filters" in ln and not ln.strip().startswith("#")
                # Allow deferred imports inside function bodies (if-guard below)
            ]
            # Only reject module-level (non-indented) imports
            top_level_imports = [
                ln for ln in lines if not ln.startswith(" ") and not ln.startswith("\t")
            ]
            assert top_level_imports == [], (
                f"{mod_name} has top-level qivc.filters import: {top_level_imports}"
            )
        except FileNotFoundError:
            pass
