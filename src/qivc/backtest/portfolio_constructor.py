"""
QIVC v3.0 portfolio constructor (Phase 1).

Turns a month's ranked composite scores into a target portfolio, implementing the
parameterised rules of the cross-validation grid:

  - **Entry threshold:** a name is *eligible* only if its composite is at or above
    the configured percentile of the universe's composites (60th / 75th / 90th).
  - **Top-N:** hold up to N names (5 or 10), highest composite first.
  - **Holding period (30 / 90 / 180 days):** a held name stays at least its holding
    period; at/after the period it is a candidate for exit and is sold only if it
    is no longer in the month's top-N. This *empirically operationalises* "optimal
    holding period" — the grid tells us which works.
  - **Sector cap 30%:** at most floor(0.30·N) NEW names per sector (forced
    holding-period keeps are honoured even if transiently over-cap).
  - **Regime overlay:** risk-off caps total equity exposure at 60% (rest → cash).
  - **Equal weight** across the final book.

Pure function; the backtest harness threads the prior month's positions in and the
new targets out. The holding period emerges from rebalance-to-rebalance state.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass

from qivc.backtest.composite_scorer import ScoredStock
from qivc.schemas import MarketRegime

_SECTOR_CAP = 0.30
_RISK_OFF_EQUITY_CAP = 0.60


@dataclass(frozen=True)
class GridConfig:
    n: int  # top-N (5 or 10)
    entry_threshold_pct: float  # 60 / 75 / 90
    holding_period_days: int  # 30 / 90 / 180

    @property
    def label(self) -> str:
        return f"N{self.n}_thr{int(self.entry_threshold_pct)}_hold{self.holding_period_days}"


@dataclass(frozen=True)
class Position:
    ticker: str
    sector: str
    weight: float
    entry_date: _dt.date
    composite: float


def _percentile_cutoff(values: list[float], pct: float) -> float:
    """Value at the *pct*-th percentile (linear, nearest-rank-ish) of values."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def construct_portfolio(
    scored: list[ScoredStock],
    prev_positions: list[Position],
    as_of: _dt.date,
    cfg: GridConfig,
    regime: MarketRegime,
    sector_cap: float = _SECTOR_CAP,
) -> list[Position]:
    """Build the target portfolio for *as_of* (equal-weight, cash residual implied)."""
    if not scored:
        return []
    by_ticker = {s.ticker: s for s in scored}
    composites = [s.composite for s in scored]
    cutoff = _percentile_cutoff(composites, cfg.entry_threshold_pct)

    eligible = [s for s in scored if s.composite >= cutoff]  # scored is pre-sorted desc
    top_n_set = {s.ticker for s in eligible[: cfg.n]}

    # 1) Survivors: still within holding period, OR still in this month's top-N.
    survivors: list[Position] = []
    for p in prev_positions:
        held_days = (as_of - p.entry_date).days
        within_hold = held_days < cfg.holding_period_days
        if within_hold or p.ticker in top_n_set:
            comp = by_ticker[p.ticker].composite if p.ticker in by_ticker else 0.0
            survivors.append(
                Position(p.ticker, p.sector, 0.0, p.entry_date, comp)  # weight set later
            )

    held_tickers = {p.ticker for p in survivors}
    sector_counts: dict[str, int] = {}
    for p in survivors:
        sector_counts[p.sector] = sector_counts.get(p.sector, 0) + 1

    # 2) Fill remaining slots from top eligible names, honouring the sector cap.
    max_per_sector = max(1, math.floor(sector_cap * cfg.n))
    final = list(survivors)
    for s in eligible:
        if len(final) >= cfg.n:
            break
        if s.ticker in held_tickers:
            continue
        if sector_counts.get(s.sector, 0) >= max_per_sector:
            continue  # sector cap on NEW entries
        final.append(Position(s.ticker, s.sector, 0.0, as_of, s.composite))
        sector_counts[s.sector] = sector_counts.get(s.sector, 0) + 1

    if not final:
        return []

    # 3) Equal weight under the regime equity budget; residual is cash (T-bills).
    budget = _RISK_OFF_EQUITY_CAP if regime.regime == "risk-off" else 1.0
    w = budget / len(final)
    return [
        Position(p.ticker, p.sector, w, p.entry_date, p.composite)
        for p in sorted(final, key=lambda p: (-p.composite, p.ticker))
    ]
