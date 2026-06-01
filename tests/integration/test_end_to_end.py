"""
End-to-end integration test — runs the full LangGraph pipeline on fixture data.

All DataAgent.fetch calls are patched to return deterministic fixture data so
this test makes no live network calls.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qivc.orchestration.graph import build_graph
from qivc.schemas import (
    EpsRevisions,
    Fundamentals,
    InsiderHistory,
    InsiderTransaction,
    Liquidity,
    MarketRegime,
    ShortInterest,
    Valuation,
)
from qivc.storage.repositories import get_run_audit

FIXTURES = Path(__file__).parent.parent / "fixtures"

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _load_fcn_transactions() -> list[InsiderTransaction]:
    """Load FCN P-code transactions from the Phase 1 fixture."""
    records = json.loads((FIXTURES / "form4_fcn.json").read_text())
    txns = []
    for rec in records:
        for owner in rec["reporting_owners"]:
            for t in rec["non_derivative_transactions"]:
                if t["Code"] != "P":
                    continue
                txns.append(
                    InsiderTransaction(
                        cik=owner["cik"],
                        name=owner["name"],
                        title=owner.get("officer_title", "Director"),
                        ticker="FCN",
                        shares=t["Shares"],
                        price=t["Price"],
                        value_usd=t["Shares"] * t["Price"],
                        transaction_date=date.fromisoformat(t["Date"]),
                        filed_date=date.fromisoformat(t["Date"]),
                        transaction_code="P",
                        is_director=owner.get("is_director", False),
                        is_officer=owner.get("is_officer", False),
                        is_ten_percent_owner=owner.get("is_ten_pct_owner", False),
                    )
                )
    return txns


def _make_fcn_fundamentals() -> Fundamentals:
    """Fundamentals from the Phase 1 FCN 10-K fixture — F-Score ~5/9."""
    raw = json.loads((FIXTURES / "tenk_fcn.json").read_text())
    ni = raw["net_income"]
    ta = raw["total_assets"]
    ocf = raw["operating_cash_flow"]
    rev = raw["revenue"]
    gp = raw["operating_income"]
    prior = raw["prior"]
    pr_ni = prior["net_income"]
    pr_ta = prior["total_assets"]
    pr_rev = prior["revenue"]
    pr_gp = prior["operating_income"]
    cur_roa = ni / ta
    pr_roa = pr_ni / pr_ta
    pr_gm = pr_gp / pr_rev if pr_rev else 0.0
    cur_gm = gp / rev if rev else 0.0
    pr_at = pr_rev / pr_ta if pr_ta else 0.0
    cur_at = rev / ta if ta else 0.0
    return Fundamentals(
        ticker="FCN",
        fiscal_period="FY2024",
        roa=cur_roa,
        ocf=ocf,
        delta_roa=cur_roa - pr_roa,  # negative → F3=0
        ocf_gt_ni=(ocf / ta) > cur_roa if ta else False,  # False → F4=0
        delta_leverage=0.0,  # no leverage data → F5=0
        delta_liquidity=0.0,  # no liquidity data → F6=0
        no_share_issuance=True,  # F7=1
        delta_gross_margin=cur_gm - pr_gm,  # positive → F8=1
        delta_asset_turnover=cur_at - pr_at,  # positive → F9=1
        gross_profit=gp,
        total_assets=ta,
    )


def _make_good_fundamentals(ticker: str) -> Fundamentals:
    """Synthetic fundamentals that score 9/9 (all components pass)."""
    return Fundamentals(
        ticker=ticker,
        fiscal_period="FY2025",
        roa=0.10,
        ocf=5_000_000.0,
        delta_roa=0.02,
        ocf_gt_ni=True,
        delta_leverage=-0.05,
        delta_liquidity=0.2,
        no_share_issuance=True,
        delta_gross_margin=0.03,
        delta_asset_turnover=0.05,
        gross_profit=1_200_000_000.0,  # GP/A = 1.2B / 3B = 0.40 ≥ 0.25 median
        total_assets=3_000_000_000.0,
    )


def _make_history(cik: str, years: int = 3) -> InsiderHistory:
    """Return a history that classifies as opportunistic (no repeated-month pattern)."""
    return InsiderHistory(cik=cik, transactions=[], years_of_history=years)


def _make_settings(db_path: str) -> Any:
    s = SimpleNamespace()
    s.db_path = db_path
    s.cmp_history_years = 3
    s.cluster_window_days = 7
    return s


# ---------------------------------------------------------------------------
# Mock agents
# ---------------------------------------------------------------------------


def _make_agents(ticker_fcn_mode: bool = True) -> Any:
    """Return a mock agents namespace backed by fixture data."""
    fcn_txns = _load_fcn_transactions()
    {t.cik for t in fcn_txns}

    agents = SimpleNamespace()

    # regime — risk-on
    async def regime_fetch(**kw: Any) -> MarketRegime:
        return MarketRegime(
            vix_60d_sma=18.0,
            credit_spread_bps=150.0,
            yield_curve_bps=30.0,
            value_growth_12m=0.02,
            regime="risk-on",
        )

    # form4 — returns FCN transactions
    async def form4_fetch(**kw: Any) -> list[InsiderTransaction]:
        return fcn_txns

    # insider_history — opportunistic (no pattern)
    async def history_fetch(**kw: Any) -> InsiderHistory:
        cik = kw.get("cik", "0")
        return _make_history(cik, years=3)

    # fundamentals — FCN fixture (F-Score ~5)
    async def fund_fetch(**kw: Any) -> Fundamentals:
        return _make_fcn_fundamentals()

    # valuation — passing (below sector median)
    async def val_fetch(**kw: Any) -> Valuation:
        ticker = kw.get("ticker", "FCN")
        return Valuation(
            ticker=ticker,
            sector="Industrials",
            gics_industry="Professional Services",
            forward_pe=None,
            ev_ebitda=8.0,
            p_tbv=None,
            p_affo=None,
            sector_median_metric_value=12.0,
        )

    # short interest — low SI, falling (case 1: always pass)
    async def si_fetch(**kw: Any) -> ShortInterest:
        return ShortInterest(
            ticker=kw.get("ticker", "FCN"),
            si_pct_float=0.03,
            prior_si_pct_float=0.04,
            report_date=date(2026, 5, 1),
            direction="falling",
        )

    # revisions — positive (pass)
    async def rev_fetch(**kw: Any) -> EpsRevisions:
        return EpsRevisions(
            ticker=kw.get("ticker", "FCN"),
            delta_30d=0.1,
            delta_60d=0.05,
            delta_90d=0.02,
            accelerating=True,
        )

    # liquidity — large cap, high ADV (pass)
    async def liq_fetch(**kw: Any) -> Liquidity:
        return Liquidity(
            ticker=kw.get("ticker", "FCN"),
            market_cap_usd=3_000_000_000.0,
            adv_20d_usd=150_000_000.0,
            next_earnings_date=None,
            has_pending_ma=False,
        )

    agents.regime = SimpleNamespace(fetch=regime_fetch)
    agents.form4 = SimpleNamespace(fetch=form4_fetch)
    agents.insider_history = SimpleNamespace(fetch=history_fetch)
    agents.fundamentals = SimpleNamespace(fetch=fund_fetch)
    agents.valuation = SimpleNamespace(fetch=val_fetch)
    agents.short_interest = SimpleNamespace(fetch=si_fetch)
    agents.revisions = SimpleNamespace(fetch=rev_fetch)
    agents.liquidity = SimpleNamespace(fetch=liq_fetch)

    return agents


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


async def test_end_to_end_fcn_rejected_at_fscore(tmp_path: Any) -> None:
    """
    Full pipeline on FCN fixture data.
    FCN has a Track A cluster (3 distinct buyers, same day) but F-Score ≈ 5/9 < 7
    so it must be rejected at the f_score gate.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = str(tmp_path / "qivc.db")
    checkpoint_path = str(tmp_path / "checkpoints.sqlite")
    settings = _make_settings(db_path)
    agents = _make_agents()

    run_id = str(uuid.uuid4())
    initial_state = {
        "run_id": run_id,
        "run_timestamp": datetime.now(UTC),
        "tickers": ["FCN"],
        "lookback_days": 14,
        "force": False,
        "regime": None,
        "transactions_by_ticker": {},
        "insider_histories": {},
        "fundamentals": {},
        "valuations": {},
        "short_interest": {},
        "revisions": {},
        "liquidity": {},
        "clusters": [],
        "filter_results": {},
        "candidates": [],
        "rejected": [],
    }

    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
        graph = build_graph(settings, agents, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": run_id}}
        final = await graph.ainvoke(initial_state, config=config)

    # FCN should have a Track A cluster (3 buyers same day → within 7-day window)
    # but F-Score is ~5/9 → rejected at f_score
    rejected = final["rejected"]
    candidates = final["candidates"]

    assert any(
        r["ticker"] == "FCN" if isinstance(r, dict) else r.ticker == "FCN" for r in rejected
    ), f"FCN should be in rejected; got rejected={rejected}, candidates={candidates}"

    # Verify run_audit has entries for every node
    audit_rows = get_run_audit(db_path, run_id)
    node_names_logged = {row["node_name"] for row in audit_rows}
    expected_nodes = {
        "regime_check",
        "ingest_form4",
        "fetch_insider_histories",
        "fetch_fundamentals",
        "fetch_valuation",
        "fetch_short_interest",
        "fetch_revisions",
        "fetch_liquidity",
        "apply_cluster_detection",
        "apply_filters",
        "apply_synthesis",
    }
    missing_nodes = expected_nodes - node_names_logged
    assert not missing_nodes, f"Missing audit entries for: {missing_nodes}"


async def test_end_to_end_good_ticker_is_candidate(tmp_path: Any) -> None:
    """A ticker with 9/9 F-Score and good data should be a candidate."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = str(tmp_path / "qivc.db")
    checkpoint_path = str(tmp_path / "checkpoints.sqlite")
    settings = _make_settings(db_path)

    # Create a synthetic ticker with 3 buyers and good fundamentals
    good_ticker = "GOOD"
    base_date = date(2026, 5, 13)
    good_txns = [
        InsiderTransaction(
            cik=f"CIK{i}",
            name=f"Buyer{i}",
            title="CEO",
            ticker=good_ticker,
            shares=1000.0,
            price=100.0,
            value_usd=100_000.0,
            transaction_date=base_date,
            filed_date=base_date,
            transaction_code="P",
            is_director=False,
            is_officer=True,
            is_ten_percent_owner=False,
        )
        for i in range(3)
    ]

    good_fundamentals = _make_good_fundamentals(good_ticker)

    class _GoodAgents:
        pass

    agents = _GoodAgents()

    async def regime_fetch(**kw: Any) -> MarketRegime:
        return MarketRegime(
            vix_60d_sma=18.0,
            credit_spread_bps=150.0,
            yield_curve_bps=30.0,
            value_growth_12m=0.02,
            regime="risk-on",
        )

    async def form4_fetch(**kw: Any) -> list[InsiderTransaction]:
        return good_txns

    async def history_fetch(**kw: Any) -> InsiderHistory:
        return _make_history(kw.get("cik", "0"), years=3)

    async def fund_fetch(**kw: Any) -> Fundamentals:
        return good_fundamentals

    async def val_fetch(**kw: Any) -> Valuation:
        return Valuation(
            ticker=good_ticker,
            sector="Industrials",
            gics_industry="Test",
            forward_pe=None,
            ev_ebitda=8.0,
            p_tbv=None,
            p_affo=None,
            sector_median_metric_value=12.0,
        )

    async def si_fetch(**kw: Any) -> ShortInterest:
        return ShortInterest(
            ticker=good_ticker,
            si_pct_float=0.02,
            prior_si_pct_float=0.03,
            report_date=date(2026, 5, 1),
            direction="falling",
        )

    async def rev_fetch(**kw: Any) -> EpsRevisions:
        return EpsRevisions(
            ticker=good_ticker,
            delta_30d=0.1,
            delta_60d=0.05,
            delta_90d=0.02,
            accelerating=True,
        )

    async def liq_fetch(**kw: Any) -> Liquidity:
        return Liquidity(
            ticker=good_ticker,
            market_cap_usd=5_000_000_000.0,
            adv_20d_usd=200_000_000.0,
            next_earnings_date=None,
            has_pending_ma=False,
        )

    agents.regime = SimpleNamespace(fetch=regime_fetch)  # type: ignore[attr-defined]
    agents.form4 = SimpleNamespace(fetch=form4_fetch)  # type: ignore[attr-defined]
    agents.insider_history = SimpleNamespace(fetch=history_fetch)  # type: ignore[attr-defined]
    agents.fundamentals = SimpleNamespace(fetch=fund_fetch)  # type: ignore[attr-defined]
    agents.valuation = SimpleNamespace(fetch=val_fetch)  # type: ignore[attr-defined]
    agents.short_interest = SimpleNamespace(fetch=si_fetch)  # type: ignore[attr-defined]
    agents.revisions = SimpleNamespace(fetch=rev_fetch)  # type: ignore[attr-defined]
    agents.liquidity = SimpleNamespace(fetch=liq_fetch)  # type: ignore[attr-defined]

    run_id = str(uuid.uuid4())
    initial_state = {
        "run_id": run_id,
        "run_timestamp": datetime.now(UTC),
        "tickers": [good_ticker],
        "lookback_days": 14,
        "force": False,
        "regime": None,
        "transactions_by_ticker": {},
        "insider_histories": {},
        "fundamentals": {},
        "valuations": {},
        "short_interest": {},
        "revisions": {},
        "liquidity": {},
        "clusters": [],
        "filter_results": {},
        "candidates": [],
        "rejected": [],
    }

    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
        graph = build_graph(settings, agents, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": run_id}}
        final = await graph.ainvoke(initial_state, config=config)

    candidates = final["candidates"]
    assert any(
        (c["ticker"] if isinstance(c, dict) else c.ticker) == good_ticker for c in candidates
    ), f"Expected {good_ticker} candidate; candidates={candidates}, rejected={final['rejected']}"


async def test_risk_off_blocks_entries(tmp_path: Any) -> None:
    """In risk-off regime without --force, graph should raise after regime_check."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from qivc.exceptions import QivcDataError

    db_path = str(tmp_path / "qivc.db")
    checkpoint_path = str(tmp_path / "checkpoints.sqlite")
    settings = _make_settings(db_path)
    agents = _make_agents()

    # Override regime to risk-off
    async def risk_off_regime(**kw: Any) -> MarketRegime:
        return MarketRegime(
            vix_60d_sma=42.0,
            credit_spread_bps=400.0,
            yield_curve_bps=-50.0,
            value_growth_12m=-0.05,
            regime="risk-off",
        )

    agents.regime = SimpleNamespace(fetch=risk_off_regime)

    run_id = str(uuid.uuid4())
    initial_state = {
        "run_id": run_id,
        "run_timestamp": datetime.now(UTC),
        "tickers": ["FCN"],
        "lookback_days": 14,
        "force": False,
        "regime": None,
        "transactions_by_ticker": {},
        "insider_histories": {},
        "fundamentals": {},
        "valuations": {},
        "short_interest": {},
        "revisions": {},
        "liquidity": {},
        "clusters": [],
        "filter_results": {},
        "candidates": [],
        "rejected": [],
    }

    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
        graph = build_graph(settings, agents, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": run_id}}
        with pytest.raises(QivcDataError):
            await graph.ainvoke(initial_state, config=config)
