"""
Conviction scorer — 4-dimension scoring matrix (PROJECT_BRIEF §5 Phase 4).

Pure functions; no I/O.

Dimensions and points
---------------------
F-Score:                 7 → 0, 8 → 2, 9 → 3   (< 7 → 0, defensive)
Insider track record:    None or < 2 prior buys → 0
                         2-3 prior buys w/ positive 12m → 2
                         4+ prior buys w/ positive 12m → 3
Valuation discount:      at median → 0
                         ≥ 10% below median → 2
                         ≥ 25% below median → 3
Cluster intensity:       1 insider single buy → 0
                         2 insiders → 2
                         3+ insiders w/ ≥1 C-suite → 3
                         5+ insiders OR (CEO + CFO both) → 4

Score → indicative size (as a FRACTION of the portfolio):
    0-3  → 0.03   (3%)
    4-6  → 0.05   (5%)
    7-9  → 0.07   (7%)
    10+  → 0.10   (10%)
"""

from __future__ import annotations

from qivc.schemas import ClusterIntensity, ConvictionScore, InsiderTrackRecord

# Discount thresholds (fraction below sector median)
_DISCOUNT_DEEP = 0.25
_DISCOUNT_MODERATE = 0.10


def _fscore_points(fscore: int) -> int:
    if fscore >= 9:
        return 3
    if fscore == 8:
        return 2
    return 0  # 7 (or anything below) → 0


def _track_record_points(tr: InsiderTrackRecord | None) -> int:
    if tr is None or tr.prior_buys < 2 or not tr.positive_12m:
        return 0
    if tr.prior_buys >= 4:
        return 3
    return 2  # 2-3 prior buys with positive 12m


def _valuation_points(discount_pct: float) -> int:
    if discount_pct >= _DISCOUNT_DEEP:
        return 3
    if discount_pct >= _DISCOUNT_MODERATE:
        return 2
    return 0


def _cluster_points(ci: ClusterIntensity) -> int:
    if ci.distinct_insiders >= 5 or (ci.has_ceo and ci.has_cfo):
        return 4
    if ci.distinct_insiders >= 3 and ci.has_csuite:
        return 3
    if ci.distinct_insiders >= 2:
        return 2
    return 0


def size_for_score(total: int) -> float:
    """Map a total conviction score to an indicative size FRACTION."""
    if total >= 10:
        return 0.10
    if total >= 7:
        return 0.07
    if total >= 4:
        return 0.05
    return 0.03


def score(
    fscore: int,
    insider_track_record: InsiderTrackRecord | None,
    valuation_discount_pct: float,
    cluster_intensity: ClusterIntensity,
) -> ConvictionScore:
    """Compute the total conviction score, per-dimension breakdown, and size."""
    breakdown = {
        "f_score": _fscore_points(fscore),
        "insider_track_record": _track_record_points(insider_track_record),
        "valuation_discount": _valuation_points(valuation_discount_pct),
        "cluster_intensity": _cluster_points(cluster_intensity),
    }
    total = sum(breakdown.values())
    return ConvictionScore(
        total=total,
        breakdown=breakdown,
        indicative_size=size_for_score(total),
    )
