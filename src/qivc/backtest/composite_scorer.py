"""
QIVC v3.0 composite scorer (Phase 1).

Replaces v2.x's boolean gate chain with a continuous, cross-sectional composite
score per stock (computed monthly over the IWM-ex-Financials/REITs universe):

    composite = 0.40*insider + 0.30*quality + 0.20*valuation + 0.10*momentum + 0.00*technical

(The 5th factor, technical-oversold, was added in Task 12; v3.0 baseline weights it
0.0 so behaviour is unchanged until a hypothesis assigns it weight.)

Each sub-score is a **cross-sectional percentile in [0, 1]** computed *within the
scored universe that month*, so the composite ranks names rather than hard-passing
them. Design choices that matter for honesty:

  - **No hard rejection.** A name with an inapplicable factor (e.g. a biotech with
    no GP/A, or a stock with no insider activity) gets a *neutral* sub-score, not
    elimination — this is the whole point of v3.0 (v2.x's 0-trade problem was that
    one inapplicable gate zeroed the name).
  - **Quality** blends F-Score (absolute, /9) with GP/A ranked **within sector**
    (Novy-Marx gross profitability is only comparable within sector).
  - **Missing data → neutral (0.5).** A component absent for the whole universe is
    a constant offset and does not change the ranking; absent for one name puts it
    at the universe median for that component.

This module is a pure function (no I/O); the PIT assembly of factor inputs is the
harness's job (later phases). Valuation/momentum inputs may be None universe-wide
until the OQ-5 sector-median pipeline exists — handled as neutral.
"""

from __future__ import annotations

from dataclasses import dataclass, field

COMPOSITE_WEIGHTS: dict[str, float] = {
    "insider": 0.40,
    "quality": 0.30,
    "valuation": 0.20,
    "momentum": 0.10,
    "technical": 0.00,  # 5th factor (Task 12); baseline v3.0 weights it 0
}


@dataclass(frozen=True)
class StockFactors:
    """Raw, point-in-time factor inputs for one stock at one rebalance date."""

    ticker: str
    sector: str
    insider_raw: float  # recency/conviction-weighted opportunistic activity (>=0; 0 if none)
    fscore: int | None  # Piotroski 0-9
    gpa: float | None  # gross profit / total assets
    valuation_raw: float | None  # cheapness composite, higher = cheaper (None if unavailable)
    momentum_raw: float | None  # forward EPS-revision signal (None if unavailable)
    technical_raw: float | None = None  # oversold blend in [0,1] (None if unavailable)


@dataclass(frozen=True)
class ScoredStock:
    ticker: str
    sector: str
    composite: float
    components: dict[str, float] = field(default_factory=dict)


def _percentile_ranks(values: list[float | None]) -> list[float]:
    """
    Cross-sectional percentile in [0, 1] for each value; None -> 0.5 (neutral).
    Ties share the mid-rank: pct = (#strictly-less + 0.5*#equal) / n_present.
    If no values are present, everything is neutral 0.5.
    """
    present = [v for v in values if v is not None]
    n = len(present)
    if n == 0:
        return [0.5] * len(values)
    out: list[float] = []
    for v in values:
        if v is None:
            out.append(0.5)
            continue
        less = sum(1 for p in present if p < v)
        equal = sum(1 for p in present if p == v)
        out.append((less + 0.5 * equal) / n)
    return out


def _quality_component(factors: list[StockFactors]) -> list[float]:
    """0.5*(F-Score/9) + 0.5*(GP/A percentile WITHIN sector); neutral if both missing."""
    # GP/A ranked within each sector.
    by_sector: dict[str, list[int]] = {}
    for i, f in enumerate(factors):
        by_sector.setdefault(f.sector, []).append(i)
    gpa_pct: list[float] = [0.5] * len(factors)
    for idxs in by_sector.values():
        ranks = _percentile_ranks([factors[i].gpa for i in idxs])
        for i, r in zip(idxs, ranks, strict=True):
            gpa_pct[i] = r

    out: list[float] = []
    for i, f in enumerate(factors):
        parts: list[float] = []
        if f.fscore is not None:
            parts.append(max(0.0, min(1.0, f.fscore / 9.0)))
        if f.gpa is not None:
            parts.append(gpa_pct[i])
        out.append(sum(parts) / len(parts) if parts else 0.5)
    return out


def score_universe(
    factors: list[StockFactors],
    weights: dict[str, float] = COMPOSITE_WEIGHTS,
) -> list[ScoredStock]:
    """Compute composite scores for the universe; returns ranked desc by composite."""
    if not factors:
        return []
    insider = _percentile_ranks([f.insider_raw for f in factors])
    quality = _quality_component(factors)
    valuation = _percentile_ranks([f.valuation_raw for f in factors])
    momentum = _percentile_ranks([f.momentum_raw for f in factors])
    technical = _percentile_ranks([f.technical_raw for f in factors])

    scored: list[ScoredStock] = []
    for i, f in enumerate(factors):
        comp = {
            "insider": insider[i],
            "quality": quality[i],
            "valuation": valuation[i],
            "momentum": momentum[i],
            "technical": technical[i],
        }
        composite = sum(weights[k] * comp[k] for k in weights)
        scored.append(
            ScoredStock(ticker=f.ticker, sector=f.sector, composite=composite, components=comp)
        )
    # Rank desc by composite; tie-break by ticker for determinism.
    scored.sort(key=lambda s: (-s.composite, s.ticker))
    return scored
