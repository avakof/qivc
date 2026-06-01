"""Piotroski F-Score filter — pure function."""

from __future__ import annotations

from qivc.schemas import CandidateInput, FilterResult, Fundamentals

_FILTER_NAME = "f_score"


def compute_fscore(f: Fundamentals) -> tuple[int, dict[str, int]]:
    """
    Compute the 9-component Piotroski F-Score from a Fundamentals object.

    Returns (total_score, per_component_dict) where each component value is 0 or 1.
    The filter layer applies the threshold; this function is purely arithmetic.
    """
    components: dict[str, int] = {
        # Profitability signals
        "roa_positive": int(f.roa > 0),
        "ocf_positive": int(f.ocf > 0),
        "delta_roa_positive": int(f.delta_roa > 0),
        "accruals_quality": int(f.ocf_gt_ni),
        # Leverage / liquidity / source-of-funds signals
        "leverage_decreased": int(f.delta_leverage < 0),
        "liquidity_increased": int(f.delta_liquidity > 0),
        "no_share_issuance": int(f.no_share_issuance),
        # Operating efficiency signals
        "gross_margin_improved": int(f.delta_gross_margin > 0),
        "asset_turnover_improved": int(f.delta_asset_turnover > 0),
    }
    return sum(components.values()), components


def apply(c: CandidateInput, threshold: int = 7) -> FilterResult:
    """Pass if F-Score >= threshold (default 7)."""
    score, components = compute_fscore(c.fundamentals)
    passed = score >= threshold
    failing = [k for k, v in components.items() if v == 0]
    reason = (
        f"F-Score {score}/9 ({'PASS' if passed else 'FAIL'}, threshold={threshold}); "
        f"failing: {failing if failing else 'none'}"
    )
    return FilterResult(
        filter_name=_FILTER_NAME,
        passed=passed,
        metric_value=float(score),
        threshold=float(threshold),
        reason=reason,
    )
