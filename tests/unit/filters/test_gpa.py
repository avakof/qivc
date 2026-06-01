"""Unit tests for the GP/A (gross profitability) filter."""

from __future__ import annotations

import pytest

from qivc.filters.gpa import apply, compute_gpa
from tests.unit.filters.test_fscore import _make_candidate, _make_fundamentals


def test_compute_gpa_basic() -> None:
    f = _make_fundamentals(gross_profit=400_000_000.0, total_assets=2_000_000_000.0)
    assert compute_gpa(f) == pytest.approx(0.20)


def test_compute_gpa_zero_assets() -> None:
    f = _make_fundamentals(gross_profit=100_000.0, total_assets=0.0)
    assert compute_gpa(f) == 0.0


def test_apply_pass_above_median() -> None:
    f = _make_fundamentals(gross_profit=600_000_000.0, total_assets=2_000_000_000.0)
    # GP/A = 0.30; median = 0.25 → PASS
    result = apply(_make_candidate(f), industry_median=0.25)
    assert result.passed is True
    assert result.metric_value == pytest.approx(0.30)
    assert result.threshold == pytest.approx(0.25)


def test_apply_fail_below_median() -> None:
    f = _make_fundamentals(gross_profit=400_000_000.0, total_assets=2_000_000_000.0)
    # GP/A = 0.20; median = 0.25 → FAIL
    result = apply(_make_candidate(f), industry_median=0.25)
    assert result.passed is False


def test_apply_pass_at_exact_median() -> None:
    """GP/A exactly at median is a PASS (≥ condition)."""
    f = _make_fundamentals(gross_profit=500_000_000.0, total_assets=2_000_000_000.0)
    # GP/A = 0.25
    result = apply(_make_candidate(f), industry_median=0.25)
    assert result.passed is True


def test_filter_name() -> None:
    f = _make_fundamentals()
    result = apply(_make_candidate(f), industry_median=0.2)
    assert result.filter_name == "gp_a"
