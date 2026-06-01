"""Unit tests for EstimatesAgent — EPS revision deltas via mocked yfinance."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from qivc.data.estimates_agent import EstimatesAgent, _to_float
from qivc.exceptions import QivcDataError
from qivc.schemas import EpsRevisions


def _make_ticker(info: dict[str, Any]) -> Any:
    t = mock.MagicMock()
    t.info = info
    return t


async def test_estimates_no_revision_data_returns_none() -> None:
    """When yfinance has no revision fields, deltas must be None."""
    info: dict[str, Any] = {"sector": "Health Care", "forwardEps": 25.0}

    with mock.patch("yfinance.Ticker", return_value=_make_ticker(info)):
        client = mock.AsyncMock()
        agent = EstimatesAgent(client=client)
        result: EpsRevisions = await agent.fetch(ticker="UNH")

    assert result.delta_30d is None
    assert result.delta_60d is None
    assert result.delta_90d is None
    assert result.accelerating is False


async def test_estimates_with_revision_data_accelerating() -> None:
    """When all deltas are present and increasing, accelerating=True."""
    info: dict[str, Any] = {
        "epsRevision30d": 0.5,
        "epsRevision60d": 0.3,
        "epsRevision90d": 0.1,
    }

    with mock.patch("yfinance.Ticker", return_value=_make_ticker(info)):
        client = mock.AsyncMock()
        agent = EstimatesAgent(client=client)
        result = await agent.fetch(ticker="UNH")

    assert result.delta_30d == pytest.approx(0.5)
    assert result.delta_60d == pytest.approx(0.3)
    assert result.delta_90d == pytest.approx(0.1)
    assert result.accelerating is True  # 0.5 > 0.3 > 0.1


async def test_estimates_not_accelerating_when_flat() -> None:
    info: dict[str, Any] = {
        "epsRevision30d": 0.2,
        "epsRevision60d": 0.3,
        "epsRevision90d": 0.1,
    }

    with mock.patch("yfinance.Ticker", return_value=_make_ticker(info)):
        client = mock.AsyncMock()
        agent = EstimatesAgent(client=client)
        result = await agent.fetch(ticker="FCN")

    # 0.2 NOT > 0.3 → not accelerating
    assert result.accelerating is False


async def test_estimates_yfinance_error_raises_data_error() -> None:
    with mock.patch("yfinance.Ticker", side_effect=Exception("network error")):
        client = mock.AsyncMock()
        agent = EstimatesAgent(client=client)

        with pytest.raises(QivcDataError):
            await agent.fetch(ticker="XYZ")


async def test_estimates_source_name() -> None:
    client = mock.AsyncMock()
    agent = EstimatesAgent(client=client)
    assert agent.source_name == "yfinance_eps_revisions"


# _to_float tests
def test_to_float_nan() -> None:
    assert _to_float(float("nan")) is None


def test_to_float_string() -> None:
    assert _to_float("1.5") == pytest.approx(1.5)


def test_to_float_none() -> None:
    assert _to_float(None) is None
