"""Gross-Profitability-to-Assets (GP/A) filter — pure function."""

from __future__ import annotations

from qivc.schemas import CandidateInput, FilterResult, Fundamentals

_FILTER_NAME = "gp_a"


def compute_gpa(f: Fundamentals) -> float:
    """Return gross_profit / total_assets; 0.0 if total_assets is zero."""
    if f.total_assets == 0.0:
        return 0.0
    return f.gross_profit / f.total_assets


def apply(c: CandidateInput, industry_median: float) -> FilterResult:
    """Pass if GP/A >= industry median."""
    gpa = compute_gpa(c.fundamentals)
    passed = gpa >= industry_median
    return FilterResult(
        filter_name=_FILTER_NAME,
        passed=passed,
        metric_value=gpa,
        threshold=industry_median,
        reason=(f"GP/A {gpa:.4f} {'≥' if passed else '<'} industry median {industry_median:.4f}"),
    )
