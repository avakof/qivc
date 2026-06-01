"""Unit tests for the risk overlay — regime, sector, and sub-sector caps."""

from __future__ import annotations

from datetime import date

import pytest

from qivc.schemas import Candidate, Cluster, InsiderTransaction, MarketRegime
from qivc.synthesis.risk_overlay import (
    SECTOR_CAP_FLAG,
    SUBSECTOR_CAP_FLAG,
    apply_regime_cap,
    apply_sector_cap,
    apply_subsector_cap,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _regime(name: str) -> MarketRegime:
    return MarketRegime(
        vix_60d_sma=18.0,
        credit_spread_bps=150.0,
        yield_curve_bps=30.0,
        value_growth_12m=0.0,
        regime=name,  # type: ignore[arg-type]
    )


def _cluster(ticker: str) -> Cluster:
    txn = InsiderTransaction(
        cik="1",
        name="Insider",
        title="CEO",
        ticker=ticker,
        shares=1000.0,
        price=100.0,
        value_usd=100_000.0,
        transaction_date=date(2026, 5, 1),
        filed_date=date(2026, 5, 1),
        transaction_code="P",
        is_director=False,
        is_officer=True,
        is_ten_percent_owner=False,
    )
    return Cluster(
        ticker=ticker,
        transactions=[txn],
        track="A",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 1),
        total_value_usd=100_000.0,
    )


def _candidate(
    ticker: str,
    sector: str,
    industry: str,
    size: float,
    conviction: int,
) -> Candidate:
    return Candidate(
        ticker=ticker,
        cluster=_cluster(ticker),
        filter_results=[],
        sector=sector,
        gics_industry_group=industry,
        conviction_score=conviction,
        indicative_size_pct=size,
    )


# ---------------------------------------------------------------------------
# Regime cap
# ---------------------------------------------------------------------------


def test_regime_cap_risk_on_no_change() -> None:
    adjusted, note = apply_regime_cap(0.07, _regime("risk-on"))
    assert adjusted == pytest.approx(0.07)
    assert "no size reduction" in note


def test_regime_cap_risk_mid_halves() -> None:
    adjusted, note = apply_regime_cap(0.10, _regime("risk-mid"))
    assert adjusted == pytest.approx(0.05)  # halved
    assert "50%" in note


def test_regime_cap_risk_off_thirds() -> None:
    adjusted, note = apply_regime_cap(0.09, _regime("risk-off"))
    assert adjusted == pytest.approx(0.03)  # third'd
    assert "33%" in note


# ---------------------------------------------------------------------------
# Sector cap — 3 healthcare candidates totaling 22% → 1 flagged
# ---------------------------------------------------------------------------


def test_sector_cap_flags_smallest_conviction() -> None:
    candidates = [
        _candidate("AAA", "Health Care", "Pharma", 0.07, conviction=9),
        _candidate("BBB", "Health Care", "Biotech", 0.07, conviction=8),
        _candidate("CCC", "Health Care", "Devices", 0.08, conviction=4),  # lowest conviction
    ]
    # total = 0.22 > 0.20 cap → flag lowest-conviction (CCC) → remaining 0.14 ≤ 0.20
    result = apply_sector_cap(candidates, sector_cap=0.20)

    flagged = [c for c in result if SECTOR_CAP_FLAG in c.flags]
    assert len(flagged) == 1
    assert flagged[0].ticker == "CCC"


def test_sector_cap_under_limit_no_flags() -> None:
    candidates = [
        _candidate("AAA", "Energy", "Oil", 0.05, conviction=7),
        _candidate("BBB", "Energy", "Gas", 0.05, conviction=6),
    ]
    result = apply_sector_cap(candidates, sector_cap=0.20)
    assert all(SECTOR_CAP_FLAG not in c.flags for c in result)


def test_sector_cap_isolated_per_sector() -> None:
    candidates = [
        _candidate("AAA", "Health Care", "Pharma", 0.15, conviction=9),
        _candidate("BBB", "Health Care", "Biotech", 0.10, conviction=5),  # HC total 0.25
        _candidate("CCC", "Energy", "Oil", 0.10, conviction=3),  # Energy total 0.10
    ]
    result = apply_sector_cap(candidates, sector_cap=0.20)
    flagged = {c.ticker for c in result if SECTOR_CAP_FLAG in c.flags}
    assert flagged == {"BBB"}  # only the HC overflow, lowest conviction


# ---------------------------------------------------------------------------
# Sub-sector cap — 2 regional bank candidates totaling 14% → 1 flagged
# ---------------------------------------------------------------------------


def test_subsector_cap_flags_smallest() -> None:
    candidates = [
        _candidate("RF", "Financials", "Regional Banks", 0.07, conviction=8),
        _candidate("KEY", "Financials", "Regional Banks", 0.07, conviction=5),  # lowest
    ]
    # total = 0.14 > 0.12 cap → flag KEY → remaining 0.07 ≤ 0.12
    result = apply_subsector_cap(candidates, subsector_cap=0.12)
    flagged = [c for c in result if SUBSECTOR_CAP_FLAG in c.flags]
    assert len(flagged) == 1
    assert flagged[0].ticker == "KEY"


def test_subsector_cap_under_limit_no_flags() -> None:
    candidates = [
        _candidate("RF", "Financials", "Regional Banks", 0.05, conviction=8),
        _candidate("KEY", "Financials", "Regional Banks", 0.05, conviction=5),
    ]
    result = apply_subsector_cap(candidates, subsector_cap=0.12)
    assert all(SUBSECTOR_CAP_FLAG not in c.flags for c in result)


def test_subsector_cap_different_industries_not_combined() -> None:
    candidates = [
        _candidate("RF", "Financials", "Regional Banks", 0.08, conviction=8),
        _candidate("JPM", "Financials", "Diversified Banks", 0.08, conviction=5),
    ]
    # Different industry groups → neither exceeds 0.12 on its own
    result = apply_subsector_cap(candidates, subsector_cap=0.12)
    assert all(SUBSECTOR_CAP_FLAG not in c.flags for c in result)


def test_empty_candidates_returns_empty() -> None:
    assert apply_sector_cap([]) == []
    assert apply_subsector_cap([]) == []
