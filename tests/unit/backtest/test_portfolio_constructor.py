"""Unit tests for the v3.0 portfolio constructor (Phase 1)."""

from __future__ import annotations

import datetime as dt

from qivc.backtest.composite_scorer import ScoredStock
from qivc.backtest.portfolio_constructor import (
    GridConfig,
    Position,
    construct_portfolio,
)
from qivc.schemas import MarketRegime

_RISK_ON = MarketRegime(
    vix_60d_sma=18.0,
    credit_spread_bps=150.0,
    yield_curve_bps=30.0,
    value_growth_12m=0.0,
    regime="risk-on",
)
_RISK_OFF = _RISK_ON.model_copy(update={"regime": "risk-off"})


def _scored(*pairs: tuple[str, str, float]) -> list[ScoredStock]:
    return [ScoredStock(t, s, c, {}) for t, s, c in pairs]


def test_topn_equal_weight_fully_invested_risk_on() -> None:
    scored = _scored(
        ("A", "Tech", 0.9),
        ("B", "Energy", 0.8),
        ("C", "Materials", 0.7),
        ("D", "Tech", 0.6),
        ("E", "Energy", 0.5),
        ("F", "Materials", 0.4),
    )
    cfg = GridConfig(n=5, entry_threshold_pct=60, holding_period_days=30)
    pf = construct_portfolio(scored, [], dt.date(2025, 1, 2), cfg, _RISK_ON)
    # threshold 60th pct of [.4..0.9] keeps top ~40%; N=5 caps the book.
    assert len(pf) <= 5
    assert abs(sum(p.weight for p in pf) - 1.0) < 1e-9  # fully invested, equal weight
    assert len({p.weight for p in pf}) == 1


def test_entry_threshold_filters_low_scores() -> None:
    scored = _scored(("A", "Tech", 0.95), ("B", "Tech", 0.5), ("C", "Energy", 0.1))
    cfg = GridConfig(n=10, entry_threshold_pct=90, holding_period_days=30)
    pf = construct_portfolio(scored, [], dt.date(2025, 1, 2), cfg, _RISK_ON)
    # 90th percentile cutoff ~0.86 -> only A eligible
    assert [p.ticker for p in pf] == ["A"]


def test_holding_period_keeps_then_sells_when_out_of_topn() -> None:
    cfg = GridConfig(n=2, entry_threshold_pct=60, holding_period_days=90)
    # Held X entered 100 days ago (past 90d window); this month it is NOT in top-2.
    prev = [Position("X", "Tech", 0.5, dt.date(2025, 1, 1), 0.9)]
    scored = _scored(("A", "Energy", 0.9), ("B", "Materials", 0.8), ("X", "Tech", 0.1))
    pf = construct_portfolio(scored, prev, dt.date(2025, 4, 11), cfg, _RISK_ON)
    assert "X" not in {p.ticker for p in pf}  # past window + out of top-N -> sold


def test_holding_period_keeps_within_window_even_if_out_of_topn() -> None:
    cfg = GridConfig(n=2, entry_threshold_pct=60, holding_period_days=90)
    # Held X entered 10 days ago (within 90d) and is NOT top-2 -> still kept.
    prev = [Position("X", "Tech", 0.5, dt.date(2025, 4, 1), 0.9)]
    scored = _scored(("A", "Energy", 0.9), ("B", "Materials", 0.8), ("X", "Tech", 0.1))
    pf = construct_portfolio(scored, prev, dt.date(2025, 4, 11), cfg, _RISK_ON)
    assert "X" in {p.ticker for p in pf}


def test_sector_cap_limits_new_entries() -> None:
    # N=10 -> max 3 new per sector. 5 Tech names ranked top -> only 3 admitted.
    scored = _scored(
        ("T1", "Tech", 0.99),
        ("T2", "Tech", 0.98),
        ("T3", "Tech", 0.97),
        ("T4", "Tech", 0.96),
        ("T5", "Tech", 0.95),
        ("E1", "Energy", 0.5),
    )
    cfg = GridConfig(n=10, entry_threshold_pct=60, holding_period_days=30)
    pf = construct_portfolio(scored, [], dt.date(2025, 1, 2), cfg, _RISK_ON)
    tech = [p for p in pf if p.sector == "Tech"]
    assert len(tech) == 3  # floor(0.30 * 10)


def test_risk_off_caps_equity_at_60pct() -> None:
    scored = _scored(("A", "Tech", 0.9), ("B", "Energy", 0.8))
    cfg = GridConfig(n=5, entry_threshold_pct=60, holding_period_days=30)
    pf = construct_portfolio(scored, [], dt.date(2025, 1, 2), cfg, _RISK_OFF)
    assert abs(sum(p.weight for p in pf) - 0.60) < 1e-9  # 40% to cash


def test_empty_scored_returns_empty() -> None:
    cfg = GridConfig(n=5, entry_threshold_pct=60, holding_period_days=30)
    assert construct_portfolio([], [], dt.date(2025, 1, 2), cfg, _RISK_ON) == []
