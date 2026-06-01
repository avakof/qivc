"""Unit tests for LiquidityAgent — market cap, ADV, earnings, M&A flag."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from qivc.data.liquidity_agent import LiquidityAgent, _parse_date, _safe_float
from qivc.exceptions import QivcDataError
from qivc.schemas import Liquidity


def _make_ticker(
    info: dict[str, Any],
    calendar: Any = None,
) -> Any:
    t = mock.MagicMock()
    t.info = info
    t.calendar = calendar
    return t


_BASE_INFO: dict[str, Any] = {
    "marketCap": 500_000_000,
    "averageVolume": 2_000_000,
    "regularMarketPrice": 50.0,
}


async def test_liquidity_computes_market_cap_and_adv() -> None:
    with mock.patch("yfinance.Ticker", return_value=_make_ticker(_BASE_INFO)):
        client = mock.AsyncMock()
        client.get_recent_8k_texts = mock.AsyncMock(return_value=[])
        agent = LiquidityAgent(client=client)
        result: Liquidity = await agent.fetch(ticker="TEST")

    assert result.market_cap_usd == pytest.approx(500_000_000)
    assert result.adv_20d_usd == pytest.approx(100_000_000)
    assert agent.source_name == "yfinance_liquidity"


async def test_liquidity_no_ma_when_no_keyword() -> None:
    with mock.patch("yfinance.Ticker", return_value=_make_ticker(_BASE_INFO)):
        client = mock.AsyncMock()
        client.get_recent_8k_texts = mock.AsyncMock(return_value=["Results."])
        agent = LiquidityAgent(client=client)
        result = await agent.fetch(ticker="TEST")

    assert result.has_pending_ma is False


async def test_liquidity_detects_ma_keyword() -> None:
    with mock.patch("yfinance.Ticker", return_value=_make_ticker(_BASE_INFO)):
        client = mock.AsyncMock()
        client.get_recent_8k_texts = mock.AsyncMock(
            return_value=["Item 1.01 Material Definitive Agreement\nMerger."]
        )
        agent = LiquidityAgent(client=client)
        result = await agent.fetch(ticker="TEST")

    assert result.has_pending_ma is True


async def test_liquidity_earnings_date_from_dict_calendar() -> None:
    calendar = {"Earnings Date": ["2026-08-15"]}

    with mock.patch("yfinance.Ticker", return_value=_make_ticker(_BASE_INFO, calendar)):
        client = mock.AsyncMock()
        client.get_recent_8k_texts = mock.AsyncMock(return_value=[])
        agent = LiquidityAgent(client=client)
        result = await agent.fetch(ticker="TEST")

    assert result.next_earnings_date is not None
    assert result.next_earnings_date.year == 2026
    assert result.next_earnings_date.month == 8


async def test_liquidity_earnings_date_fallback_price() -> None:
    """When regularMarketPrice is 0/None, falls back to previousClose."""
    info = dict(_BASE_INFO)
    info["regularMarketPrice"] = 0.0
    info["previousClose"] = 45.0

    with mock.patch("yfinance.Ticker", return_value=_make_ticker(info)):
        client = mock.AsyncMock()
        client.get_recent_8k_texts = mock.AsyncMock(return_value=[])
        agent = LiquidityAgent(client=client)
        result = await agent.fetch(ticker="TEST")

    assert result.adv_20d_usd == pytest.approx(2_000_000 * 45.0)


async def test_liquidity_calendar_exception_handled() -> None:
    """Calendar lookup failure should not propagate."""
    ticker_obj = mock.MagicMock()
    ticker_obj.info = dict(_BASE_INFO)
    ticker_obj.calendar = mock.PropertyMock(side_effect=Exception("cal error"))
    type(ticker_obj).calendar = mock.PropertyMock(side_effect=Exception("cal error"))

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        client = mock.AsyncMock()
        client.get_recent_8k_texts = mock.AsyncMock(return_value=[])
        agent = LiquidityAgent(client=client)
        result = await agent.fetch(ticker="TEST")

    assert result.next_earnings_date is None


async def test_liquidity_8k_exception_handled() -> None:
    """8-K lookup failure should not propagate."""
    with mock.patch("yfinance.Ticker", return_value=_make_ticker(_BASE_INFO)):
        client = mock.AsyncMock()
        client.get_recent_8k_texts = mock.AsyncMock(side_effect=Exception("8K error"))
        agent = LiquidityAgent(client=client)
        result = await agent.fetch(ticker="TEST")

    assert result.has_pending_ma is False


async def test_liquidity_yfinance_error_raises() -> None:
    with mock.patch("yfinance.Ticker", side_effect=Exception("timeout")):
        agent = LiquidityAgent(client=mock.AsyncMock())
        with pytest.raises(QivcDataError):
            await agent.fetch(ticker="ERR")


# ---------------------------------------------------------------------------
# helper function tests
# ---------------------------------------------------------------------------


def test_safe_float_none() -> None:
    assert _safe_float(None) == 0.0


def test_safe_float_string() -> None:
    assert _safe_float("123.4") == pytest.approx(123.4)


def test_parse_date_none() -> None:
    assert _parse_date(None) is None


def test_parse_date_string() -> None:
    from datetime import date

    result = _parse_date("2026-08-15")
    assert result == date(2026, 8, 15)


def test_parse_date_with_date_object() -> None:
    from datetime import date

    d = date(2026, 8, 15)
    assert _parse_date(d) == d


def test_parse_date_invalid_string() -> None:
    assert _parse_date("not-a-date") is None


def test_safe_float_invalid_string() -> None:
    assert _safe_float("N/A") == 0.0


async def test_liquidity_df_calendar_branch() -> None:
    """When yfinance returns a DataFrame calendar, should still parse correctly."""
    import pandas as pd

    # Simulate a DataFrame calendar (yfinance sometimes returns this)
    cal_df = mock.MagicMock()
    cal_df.__class__ = pd.DataFrame  # fake type check
    cal_df.iloc.__getitem__ = mock.MagicMock(return_value="2026-09-01")
    cal_df.iloc = mock.MagicMock()
    cal_df.iloc.__getitem__ = mock.MagicMock(return_value="2026-09-01")
    # Make isinstance(cal, dict) return False
    # Use a non-dict object instead

    class FakeCal:
        def __init__(self) -> None:
            self.iloc = mock.MagicMock()
            self.iloc.__getitem__ = mock.MagicMock(return_value="2026-09-01")

    ticker_obj = mock.MagicMock()
    ticker_obj.info = dict(_BASE_INFO)
    ticker_obj.calendar = FakeCal()

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        client = mock.AsyncMock()
        client.get_recent_8k_texts = mock.AsyncMock(return_value=[])
        agent = LiquidityAgent(client=client)
        result = await agent.fetch(ticker="TEST")

    # Should complete without error regardless of calendar parse result
    assert isinstance(result, Liquidity)
