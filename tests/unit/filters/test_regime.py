"""Unit tests for the regime filter."""

from __future__ import annotations

import pytest

from qivc.filters.regime import apply
from qivc.schemas import MarketRegime


def _regime(regime: str, vix: float = 18.0) -> MarketRegime:
    return MarketRegime(
        vix_60d_sma=vix,
        credit_spread_bps=150.0,
        yield_curve_bps=30.0,
        value_growth_12m=0.02,
        regime=regime,  # type: ignore[arg-type]
    )


def test_risk_on_passes() -> None:
    result = apply(_regime("risk-on", vix=18.0))
    assert result.passed is True
    assert result.filter_name == "regime"
    assert "risk-on" in result.reason


def test_risk_mid_passes_with_annotation() -> None:
    result = apply(_regime("risk-mid", vix=29.0))
    assert result.passed is True
    assert "risk-mid" in result.reason
    assert "halved" in result.reason


def test_risk_off_fails() -> None:
    result = apply(_regime("risk-off", vix=42.0))
    assert result.passed is False
    assert "BLOCKED" in result.reason
    assert result.metric_value == pytest.approx(42.0)


@pytest.mark.parametrize(
    "regime,expected_pass",
    [
        ("risk-on", True),
        ("risk-mid", True),
        ("risk-off", False),
    ],
)
def test_all_three_states(regime: str, expected_pass: bool) -> None:
    result = apply(_regime(regime))
    assert result.passed is expected_pass
