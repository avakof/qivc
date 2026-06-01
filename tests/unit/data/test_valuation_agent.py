"""Unit tests for ValuationAgent — yfinance mock + sector-median lookup."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from qivc.data.valuation_agent import ValuationAgent, _to_float
from qivc.schemas import Valuation

_MOCK_INFO: dict[str, Any] = {
    "sector": "Health Care",
    "industry": "Managed Health Care",
    "forwardPE": 14.5,
    "enterpriseToEbitda": 10.2,
    "priceToBook": 3.1,
    "marketCap": 250_000_000_000,
}


def _mock_yf_ticker(info: dict[str, Any]) -> Any:
    ticker_obj = mock.MagicMock()
    ticker_obj.info = info
    return ticker_obj


async def test_valuation_maps_yfinance_fields() -> None:
    with mock.patch("yfinance.Ticker", return_value=_mock_yf_ticker(_MOCK_INFO)):
        client = mock.AsyncMock()
        agent = ValuationAgent(client=client)
        result: Valuation = await agent.fetch(ticker="UNH")

    assert result.ticker == "UNH"
    assert result.sector == "Health Care"
    assert result.gics_industry == "Managed Health Care"
    assert result.forward_pe == pytest.approx(14.5)
    assert result.ev_ebitda == pytest.approx(10.2)
    assert result.p_tbv == pytest.approx(3.1)
    assert result.p_affo is None  # not available via yfinance


async def test_valuation_calls_sector_median_provider() -> None:
    """sector_median_provider should be called with the right sector and metric."""
    calls: list[tuple[str, str]] = []

    def track_median(sector: str, metric: str) -> float | None:
        calls.append((sector, metric))
        return 12.0

    with mock.patch("yfinance.Ticker", return_value=_mock_yf_ticker(_MOCK_INFO)):
        client = mock.AsyncMock()
        agent = ValuationAgent(client=client, sector_median_provider=track_median)
        result = await agent.fetch(ticker="UNH")

    assert len(calls) == 1
    sector, metric = calls[0]
    assert sector == "Health Care"
    assert metric == "forward_pe"  # Health Care → forward_pe per spec
    assert result.sector_median_metric_value == pytest.approx(12.0)


async def test_valuation_none_when_yfinance_missing_field() -> None:
    """Missing yfinance fields should produce None, not raise."""
    sparse_info: dict[str, Any] = {
        "sector": "Financials",
        "industry": "Banks",
    }
    with mock.patch("yfinance.Ticker", return_value=_mock_yf_ticker(sparse_info)):
        client = mock.AsyncMock()
        agent = ValuationAgent(client=client)
        result = await agent.fetch(ticker="JPM")

    assert result.forward_pe is None
    assert result.ev_ebitda is None
    assert result.p_tbv is None


# ---------------------------------------------------------------------------
# _to_float helper
# ---------------------------------------------------------------------------


def test_to_float_nan_returns_none() -> None:

    assert _to_float(float("nan")) is None


def test_to_float_valid() -> None:
    assert _to_float(3.14) == pytest.approx(3.14)
    assert _to_float("5.5") == pytest.approx(5.5)


def test_to_float_none_input() -> None:
    assert _to_float(None) is None
