"""Unit tests for the valuation filter."""

from __future__ import annotations

import pytest

from qivc.filters.valuation import SECTOR_VALUATION_METRIC, SECTOR_VALUATION_PREMIUM, apply
from qivc.schemas import Valuation
from tests.unit.filters.test_fscore import _make_candidate, _make_fundamentals


def _candidate_with_valuation(
    sector: str,
    metric_name: str,
    metric_val: float | None,
    sector_median: float | None,
) -> object:
    f = _make_fundamentals()
    c = _make_candidate(f)
    val_kwargs = {
        "ticker": "TEST",
        "sector": sector,
        "gics_industry": "Test Industry",
        "forward_pe": None,
        "ev_ebitda": None,
        "p_tbv": None,
        "p_affo": None,
        "sector_median_metric_value": sector_median,
    }
    val_kwargs[metric_name] = metric_val  # type: ignore[literal-required]
    return c.model_copy(update={"valuation": Valuation(**val_kwargs)})  # type: ignore[arg-type]


def test_pass_within_premium_health_care() -> None:
    """Health Care uses forward_pe with 10% premium; 15.0 <= 16.0 * 1.10 → PASS."""
    c = _candidate_with_valuation("Health Care", "forward_pe", 15.0, 16.0)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True
    assert result.threshold == pytest.approx(16.0 * 1.10)


def test_fail_above_premium() -> None:
    """forward_pe 20.0 > 16.0 * 1.10 = 17.6 → FAIL."""
    c = _candidate_with_valuation("Health Care", "forward_pe", 20.0, 16.0)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is False


def test_pass_at_exact_threshold() -> None:
    """Metric exactly equal to threshold is PASS (≤ condition)."""
    premium = SECTOR_VALUATION_PREMIUM["Health Care"]
    median = 16.0
    exact = median * premium
    c = _candidate_with_valuation("Health Care", "forward_pe", exact, median)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True


def test_industrials_ev_ebitda_no_premium() -> None:
    """Industrials use ev_ebitda with 0% premium (factor=1.0)."""
    c = _candidate_with_valuation("Industrials", "ev_ebitda", 10.0, 10.0)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True
    assert result.threshold == pytest.approx(10.0)

    c_fail = _candidate_with_valuation("Industrials", "ev_ebitda", 10.01, 10.0)
    assert apply(c_fail).passed is False  # type: ignore[arg-type]


def test_financials_p_tbv() -> None:
    """Financials use p_tbv."""
    metric = SECTOR_VALUATION_METRIC["Financials"]
    assert metric == "p_tbv"
    c = _candidate_with_valuation("Financials", "p_tbv", 1.0, 1.5)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True


def test_unverifiable_missing_metric() -> None:
    """When the primary metric is None, passed must be None."""
    c = _candidate_with_valuation("Health Care", "forward_pe", None, 16.0)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is None
    assert "UNVERIFIABLE" in result.reason


def test_unverifiable_missing_sector_median() -> None:
    """When sector_median is None, passed must be None."""
    c = _candidate_with_valuation("Health Care", "forward_pe", 14.0, None)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is None
    assert "UNVERIFIABLE" in result.reason


def test_unknown_sector_defaults_forward_pe() -> None:
    """Unknown sector maps to forward_pe with premium 1.0."""
    c = _candidate_with_valuation("Unknown Sector", "forward_pe", 10.0, 12.0)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True


def test_filter_name() -> None:
    c = _candidate_with_valuation("Health Care", "forward_pe", 14.0, 16.0)
    assert apply(c).filter_name == "valuation"  # type: ignore[arg-type]
