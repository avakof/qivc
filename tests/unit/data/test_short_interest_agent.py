"""Unit tests for ShortInterestAgent — direction computation edge cases."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from qivc.data.short_interest_agent import ShortInterestAgent, _compute_direction
from qivc.schemas import ShortInterest

# ---------------------------------------------------------------------------
# _compute_direction unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current, prior, expected",
    [
        (0.10, 0.09, "rising"),  # delta = +0.01, above threshold (0.005)
        (0.05, 0.05, "flat"),  # delta = 0
        (0.05, 0.054, "flat"),  # delta = -0.004, within flat band
        (0.05, 0.056, "falling"),  # delta = -0.006, below threshold
        (0.20, 0.00, "rising"),  # large increase
        (0.00, 0.20, "falling"),  # large decrease
        (0.10, 0.1049, "flat"),  # just inside flat band
        (0.10, 0.1051, "falling"),  # just outside flat band
    ],
)
def test_compute_direction(current: float, prior: float, expected: str) -> None:
    assert _compute_direction(current, prior) == expected


# ---------------------------------------------------------------------------
# ShortInterestAgent integration tests with mocked yfinance
# ---------------------------------------------------------------------------


def _make_info(
    short_pct: float,
    shares_short_prior: int,
    shares_outstanding: int,
    date_ts: int = 1_700_000_000,
) -> dict[str, Any]:
    return {
        "shortPercentOfFloat": short_pct,
        "sharesShortPriorMonth": shares_short_prior,
        "sharesOutstanding": shares_outstanding,
        "sharesShortPreviousMonthDate": date_ts,
    }


async def test_si_agent_rising() -> None:
    """Rising short interest should be detected."""
    info = _make_info(
        short_pct=0.10,
        shares_short_prior=8_000_000,
        shares_outstanding=100_000_000,
    )
    ticker_obj = mock.MagicMock()
    ticker_obj.info = info

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        client = mock.AsyncMock()
        agent = ShortInterestAgent(client=client)
        result: ShortInterest = await agent.fetch(ticker="TEST")

    assert result.direction == "rising"
    assert result.si_pct_float == pytest.approx(0.10)
    assert agent.source_name == "yfinance_short_interest"


async def test_si_agent_flat() -> None:
    info = _make_info(
        short_pct=0.10,
        shares_short_prior=10_200_000,
        shares_outstanding=100_000_000,
    )
    ticker_obj = mock.MagicMock()
    ticker_obj.info = info

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        agent = ShortInterestAgent(client=mock.AsyncMock())
        result = await agent.fetch(ticker="TEST")

    assert result.direction == "flat"


async def test_si_agent_falling() -> None:
    info = _make_info(
        short_pct=0.05,
        shares_short_prior=12_000_000,
        shares_outstanding=100_000_000,
    )
    ticker_obj = mock.MagicMock()
    ticker_obj.info = info

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        agent = ShortInterestAgent(client=mock.AsyncMock())
        result = await agent.fetch(ticker="TEST")

    assert result.direction == "falling"


async def test_si_agent_missing_prior_data() -> None:
    """If prior data is absent, prior_pct falls back to current → flat."""
    info: dict[str, Any] = {"shortPercentOfFloat": 0.07}
    ticker_obj = mock.MagicMock()
    ticker_obj.info = info

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        agent = ShortInterestAgent(client=mock.AsyncMock())
        result = await agent.fetch(ticker="TEST")

    assert result.direction == "flat"
    assert result.prior_si_pct_float == pytest.approx(0.07)


async def test_si_agent_non_numeric_si_pct_defaults_zero() -> None:
    """Non-numeric shortPercentOfFloat should default to 0 without raising."""
    info: dict[str, Any] = {
        "shortPercentOfFloat": "N/A",
        "sharesShortPriorMonth": None,
        "sharesOutstanding": None,
    }
    ticker_obj = mock.MagicMock()
    ticker_obj.info = info

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        agent = ShortInterestAgent(client=mock.AsyncMock())
        result = await agent.fetch(ticker="TEST")

    assert result.si_pct_float == pytest.approx(0.0)


async def test_si_agent_no_report_date_defaults_today() -> None:
    """Missing sharesShortPreviousMonthDate should default to today."""
    from datetime import date

    info: dict[str, Any] = {
        "shortPercentOfFloat": 0.05,
        "sharesShortPriorMonth": 5_000_000,
        "sharesOutstanding": 100_000_000,
        # no sharesShortPreviousMonthDate
    }
    ticker_obj = mock.MagicMock()
    ticker_obj.info = info

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        agent = ShortInterestAgent(client=mock.AsyncMock())
        result = await agent.fetch(ticker="TEST")

    assert result.report_date == date.today()


async def test_si_agent_yfinance_error_raises() -> None:
    from qivc.exceptions import QivcDataError

    with mock.patch("yfinance.Ticker", side_effect=Exception("timeout")):
        agent = ShortInterestAgent(client=mock.AsyncMock())
        with pytest.raises(QivcDataError):
            await agent.fetch(ticker="ERR")


async def test_si_agent_bad_timestamp_defaults_today() -> None:
    """OSError in date.fromtimestamp should default to today."""
    from datetime import date

    info: dict[str, Any] = {
        "shortPercentOfFloat": 0.05,
        "sharesShortPriorMonth": 5_000_000,
        "sharesOutstanding": 100_000_000,
        "sharesShortPreviousMonthDate": 99999999999999,  # out-of-range timestamp
    }
    ticker_obj = mock.MagicMock()
    ticker_obj.info = info

    with mock.patch("yfinance.Ticker", return_value=ticker_obj):
        agent = ShortInterestAgent(client=mock.AsyncMock())
        result = await agent.fetch(ticker="TEST")

    assert result.report_date == date.today()
