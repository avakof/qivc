"""
Short-interest filter — 4-case logic matrix — pure function.

Case 1: SI < 5%                     → PASS (regardless of direction)
Case 2: SI 5-15%, falling           → PASS (conviction upgrade)
Case 3: SI > 15%, rising            → conditional PASS (extra strength required)
Case 4: SI > 15%, falling or flat   → PASS (conviction upgrade)

"Extra strength signals" (case 3) are computed from the CandidateInput:
  - Any officer buy ≥ $1 000 000 in the cluster
  - F-Score ≥ 8
  - GP/A ≥ 0.40 (approximate top-quartile proxy)
"""

from __future__ import annotations

from qivc.schemas import CandidateInput, FilterResult

_FILTER_NAME = "short_interest"
_SI_LOW = 0.05  # 5%
_SI_HIGH = 0.15  # 15%
_CSUIT_BUY_USD = 1_000_000.0
_GPA_TOP_QUARTILE_PROXY = 0.40
_FSCORE_STRONG = 8


def _has_extra_strength(c: CandidateInput) -> bool:
    """Return True if any extra-strength signal is present (for case 3 override)."""
    # C-suite buy ≥ $1M in the cluster
    if any(t.is_officer and t.value_usd >= _CSUIT_BUY_USD for t in c.transactions):
        return True

    # F-Score ≥ 8 (inline; avoids import from fscore to keep the dependency cycle clear)
    f = c.fundamentals
    score = sum(
        [
            int(f.roa > 0),
            int(f.ocf > 0),
            int(f.delta_roa > 0),
            int(f.ocf_gt_ni),
            int(f.delta_leverage < 0),
            int(f.delta_liquidity > 0),
            int(f.no_share_issuance),
            int(f.delta_gross_margin > 0),
            int(f.delta_asset_turnover > 0),
        ]
    )
    if score >= _FSCORE_STRONG:
        return True

    # GP/A top-quartile proxy
    return f.total_assets > 0 and (f.gross_profit / f.total_assets) >= _GPA_TOP_QUARTILE_PROXY


def apply(c: CandidateInput) -> FilterResult:
    """Apply the 4-case short-interest logic matrix."""
    si = c.short_interest.si_pct_float
    direction = c.short_interest.direction

    # Case 1 — low SI (< 5%), always pass
    if si < _SI_LOW:
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=True,
            metric_value=si,
            threshold=_SI_LOW,
            reason=f"SI {si:.1%} below 5% threshold; direction={direction}",
        )

    # Case 2 — medium SI (5-15%), falling → conviction upgrade
    if si <= _SI_HIGH and direction == "falling":
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=True,
            metric_value=si,
            threshold=_SI_HIGH,
            reason=f"SI {si:.1%} falling in 5-15% band; conviction upgrade",
        )

    # Medium SI, not falling → neutral PASS
    if si <= _SI_HIGH:
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=True,
            metric_value=si,
            threshold=_SI_HIGH,
            reason=f"SI {si:.1%} in 5-15% band, direction={direction}; neutral",
        )

    # High SI (> 15%)
    if direction == "rising":
        # Case 3 — conditional PASS if extra strength present
        extra = _has_extra_strength(c)
        if extra:
            return FilterResult(
                filter_name=_FILTER_NAME,
                passed=True,
                metric_value=si,
                threshold=_SI_HIGH,
                reason=(
                    f"SI {si:.1%} rising above 15%; passed on extra strength signals "
                    f"(F-Score≥{_FSCORE_STRONG} or C-suite buy≥$1M or high GP/A)"
                ),
            )
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=False,
            metric_value=si,
            threshold=_SI_HIGH,
            reason=f"SI {si:.1%} rising above 15%; no extra strength signals; BLOCKED",
        )

    # Case 4 — high SI, falling or flat → conviction upgrade
    return FilterResult(
        filter_name=_FILTER_NAME,
        passed=True,
        metric_value=si,
        threshold=_SI_HIGH,
        reason=f"SI {si:.1%} declining from high level ({direction}); conviction upgrade",
    )
