"""
Risk overlay — regime cap, sector cap, sub-sector cap.

Pure functions; no I/O.

Regime cap (per user-confirmed spec: halve / third — see PHASE4_NOTES.md):
    risk-on  → no change
    risk-mid → 50% of indicative (halved)
    risk-off → 33.3% of indicative (third'd)

Sector / sub-sector caps:
    Group candidates by GICS sector (resp. industry group).  If the cumulative
    indicative size exceeds the cap, flag candidates from SMALLEST conviction
    first as EXCEEDS_SECTOR_CAP (resp. EXCEEDS_SUBSECTOR_CAP) until the remaining
    unflagged cumulative size is within the cap.
"""

from __future__ import annotations

from qivc.schemas import Candidate, MarketRegime

_REGIME_MULTIPLIER = {
    "risk-on": 1.0,
    "risk-mid": 0.5,  # halved
    "risk-off": 1.0 / 3.0,  # third'd
}

SECTOR_CAP_FLAG = "EXCEEDS_SECTOR_CAP"
SUBSECTOR_CAP_FLAG = "EXCEEDS_SUBSECTOR_CAP"


def apply_regime_cap(indicative_size: float, regime: MarketRegime) -> tuple[float, str]:
    """Scale *indicative_size* by the regime multiplier; return (adjusted, explanation)."""
    multiplier = _REGIME_MULTIPLIER.get(regime.regime, 1.0)
    adjusted = indicative_size * multiplier
    if multiplier == 1.0:
        explanation = f"Regime {regime.regime}: no size reduction (100%)"
    else:
        explanation = (
            f"Regime {regime.regime}: size reduced to {multiplier:.0%} "
            f"({indicative_size:.1%} → {adjusted:.1%})"
        )
    return adjusted, explanation


def _apply_group_cap(
    candidates: list[Candidate],
    cap: float,
    group_key: str,
    flag: str,
) -> list[Candidate]:
    """Generic grouped-cap: flag smallest-conviction candidates until each group fits."""
    # Bucket indices by group value
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(candidates):
        key = getattr(c, group_key)
        groups.setdefault(key, []).append(i)

    flagged_idx: set[int] = set()
    for indices in groups.values():
        running = sum(candidates[i].indicative_size_pct for i in indices)
        if running <= cap:
            continue
        # Flag smallest conviction first (ties broken by smaller size, then index)
        ordered = sorted(
            indices,
            key=lambda i: (
                candidates[i].conviction_score,
                candidates[i].indicative_size_pct,
                i,
            ),
        )
        for i in ordered:
            if running <= cap:
                break
            flagged_idx.add(i)
            running -= candidates[i].indicative_size_pct

    result: list[Candidate] = []
    for i, c in enumerate(candidates):
        if i in flagged_idx and flag not in c.flags:
            result.append(c.model_copy(update={"flags": [*c.flags, flag]}))
        else:
            result.append(c)
    return result


def apply_sector_cap(candidates: list[Candidate], sector_cap: float = 0.20) -> list[Candidate]:
    """Flag candidates whose GICS sector exceeds *sector_cap* cumulative size."""
    return _apply_group_cap(candidates, sector_cap, "sector", SECTOR_CAP_FLAG)


def apply_subsector_cap(
    candidates: list[Candidate], subsector_cap: float = 0.12
) -> list[Candidate]:
    """Flag candidates whose GICS industry group exceeds *subsector_cap*."""
    return _apply_group_cap(candidates, subsector_cap, "gics_industry_group", SUBSECTOR_CAP_FLAG)
