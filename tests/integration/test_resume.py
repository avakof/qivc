"""
Checkpoint resumability test.

Strategy:
  1. Build a graph whose `fetch_fundamentals` node raises on its first call.
  2. Invoke — the graph errors mid-run; SqliteSaver has checkpointed the
     state up to (but not including) the failed node.
  3. Flip the agent so it succeeds, re-invoke with the SAME thread_id.
  4. Verify the run completes and fundamentals were fetched on resume —
     and that nodes which already succeeded are not re-executed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
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


def _make_settings(db_path: str) -> Any:
    s = SimpleNamespace()
    s.db_path = db_path
    s.cmp_history_years = 3
    s.cluster_window_days = 7
    return s


def _base_txns() -> list[InsiderTransaction]:
    base = date(2026, 5, 13)
    return [
        InsiderTransaction(
            cik=f"CIK{i}",
            name=f"Buyer{i}",
            title="CEO",
            ticker="RES",
            shares=1000.0,
            price=100.0,
            value_usd=100_000.0,
            transaction_date=base,
            filed_date=base,
            transaction_code="P",
            is_director=False,
            is_officer=True,
            is_ten_percent_owner=False,
        )
        for i in range(3)
    ]


def _fundamentals_for(ticker: str) -> Fundamentals:
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
        gross_profit=1_200_000_000.0,
        total_assets=3_000_000_000.0,
    )


def _make_agents() -> Any:
    agents = SimpleNamespace()

    async def regime_fetch(**kw: Any) -> MarketRegime:
        return MarketRegime(
            vix_60d_sma=18.0,
            credit_spread_bps=150.0,
            yield_curve_bps=30.0,
            value_growth_12m=0.02,
            regime="risk-on",
        )

    async def form4_fetch(**kw: Any) -> list[InsiderTransaction]:
        return _base_txns()

    async def history_fetch(**kw: Any) -> InsiderHistory:
        # P-buys in 3 distinct prior calendar years (February, not the candidate's
        # May) → classifiable but opportunistic. Post-OQ-4 the classifier measures
        # years from these transactions, so the history needs real prior trades.
        cik = kw.get("cik", "0")
        yr = date.today().year
        txns = [
            InsiderTransaction(
                cik=cik,
                name="Prior Buyer",
                title="CEO",
                ticker="RES",
                shares=100.0,
                price=10.0,
                value_usd=1000.0,
                transaction_date=date(yr - k, 2, 1),
                filed_date=date(yr - k, 2, 1),
                transaction_code="P",
                is_director=False,
                is_officer=True,
                is_ten_percent_owner=False,
            )
            for k in (1, 2, 3)
        ]
        return InsiderHistory(cik=cik, transactions=txns, years_of_history=3)

    async def fund_fetch(**kw: Any) -> Fundamentals:
        return _fundamentals_for(kw.get("ticker", "RES"))

    async def val_fetch(**kw: Any) -> Valuation:
        return Valuation(
            ticker="RES",
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
            ticker="RES",
            si_pct_float=0.02,
            prior_si_pct_float=0.03,
            report_date=date(2026, 5, 1),
            direction="falling",
        )

    async def rev_fetch(**kw: Any) -> EpsRevisions:
        return EpsRevisions(
            ticker="RES",
            delta_30d=0.1,
            delta_60d=0.05,
            delta_90d=0.02,
            accelerating=True,
        )

    async def liq_fetch(**kw: Any) -> Liquidity:
        return Liquidity(
            ticker="RES",
            market_cap_usd=5_000_000_000.0,
            adv_20d_usd=200_000_000.0,
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


class _FundamentalsNodeCounter:
    """Wraps the real fetch_fundamentals node so it raises on its FIRST call only."""

    def __init__(self, real_node: Any) -> None:
        self._real = real_node
        self.call_count = 0

    async def __call__(self, state: Any) -> dict[str, Any]:
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("Simulated mid-run crash in fetch_fundamentals")
        return await self._real(state)


def _initial_state(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "run_timestamp": datetime.now(UTC),
        "tickers": ["RES"],
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


async def test_checkpoint_resume_after_failure(tmp_path: Any) -> None:
    from unittest import mock

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from qivc.orchestration import nodes as nodes_mod

    db_path = str(tmp_path / "qivc.db")
    checkpoint_path = str(tmp_path / "checkpoints.sqlite")
    settings = _make_settings(db_path)
    agents = _make_agents()

    # Wrap make_nodes so the fetch_fundamentals NODE raises on its first call.
    counter_holder: dict[str, _FundamentalsNodeCounter] = {}
    real_make_nodes = nodes_mod.make_nodes

    def patched_make_nodes(a: Any, s: Any) -> dict[str, Any]:
        node_map = real_make_nodes(a, s)
        counter = _FundamentalsNodeCounter(node_map["fetch_fundamentals"])
        counter_holder["c"] = counter
        node_map["fetch_fundamentals"] = counter
        return node_map

    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": run_id}}

    with mock.patch("qivc.orchestration.graph.make_nodes", side_effect=patched_make_nodes):
        async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
            graph = build_graph(settings, agents, checkpointer=checkpointer)
            counter = counter_holder["c"]

            # First invocation — fetch_fundamentals node raises, run aborts
            with pytest.raises(RuntimeError):
                await graph.ainvoke(_initial_state(run_id), config=config)

            assert counter.call_count == 1, "fundamentals node should have run once"

            # State should be checkpointed: regime + ingest completed
            snapshot = await graph.aget_state(config)
            assert snapshot is not None
            checkpointed = snapshot.values
            assert checkpointed.get("transactions_by_ticker"), (
                "expected transactions to be checkpointed before failure"
            )

            # Re-invoke with same thread_id and None input → resume from checkpoint
            final = await graph.ainvoke(None, config=config)

    # On resume, fundamentals node should have been retried and succeeded
    assert counter.call_count == 2, "fundamentals node should be retried once on resume"

    # The pipeline should now complete: RES is a candidate
    candidates = final["candidates"]
    assert any((c["ticker"] if isinstance(c, dict) else c.ticker) == "RES" for c in candidates), (
        f"Expected RES candidate after resume; got {final['candidates']}, {final['rejected']}"
    )

    # Audit log proves RESUMPTION, not a full restart:
    #   - fetch_fundamentals completed exactly once (the successful resume run;
    #     the first attempt crashed inside the test wrapper before the audited
    #     node body executed, so it logs no row)
    #   - regime_check and ingest_form4 ran exactly once each — they were NOT
    #     re-executed on resume because their checkpointed writes were replayed
    audit_rows = get_run_audit(db_path, run_id)
    fund_rows = [r for r in audit_rows if r["node_name"] == "fetch_fundamentals"]
    assert len(fund_rows) == 1, f"expected 1 fundamentals success row, got {len(fund_rows)}"

    regime_rows = [r for r in audit_rows if r["node_name"] == "regime_check"]
    ingest_rows = [r for r in audit_rows if r["node_name"] == "ingest_form4"]
    assert len(regime_rows) == 1, "regime_check must NOT re-run on resume"
    assert len(ingest_rows) == 1, "ingest_form4 must NOT re-run on resume"
