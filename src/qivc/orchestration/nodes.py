"""
Node functions for the QIVC LangGraph pipeline.

Design:
  - Each node is an async function: `async def node_name(state: PipelineState) -> dict`.
  - Agents are injected via `make_nodes(agents, settings)`, so nodes are easily
    mockable in tests.
  - Every node entry/exit (and error) is logged to DuckDB run_audit via `_audited`.
  - Per-ticker agent failures inside the fan-out fetch nodes are tolerated
    (logged as warnings; the ticker is later marked UNVERIFIABLE). A node-level
    exception (e.g. risk-off in regime_check) propagates and aborts the run,
    leaving a resumable checkpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import structlog

from qivc.exceptions import QivcDataError
from qivc.filters import cluster_detector as cluster_mod
from qivc.filters import fscore as fscore_mod
from qivc.filters import gpa as gpa_mod
from qivc.filters import insider_classifier as cmp_mod
from qivc.filters import liquidity as liquidity_mod
from qivc.filters import regime as regime_mod
from qivc.filters import revisions as revisions_mod
from qivc.filters import short_interest as si_mod
from qivc.filters import valuation as valuation_mod
from qivc.orchestration.state import PipelineState
from qivc.schemas import (
    Candidate,
    CandidateInput,
    Cluster,
    ClusterIntensity,
    EpsRevisions,
    FilterResult,
    Fundamentals,
    InsiderHistory,
    InsiderTransaction,
    Liquidity,
    RejectedCandidate,
    ShortInterest,
    Valuation,
)
from qivc.storage import repositories as repo
from qivc.synthesis import conviction as conviction_mod
from qivc.synthesis import risk_overlay as overlay_mod

log = structlog.get_logger(__name__)

# Target position size used for ADV liquidity check (configurable in Phase 4)
_TARGET_POSITION_USD = 1_000_000.0
# Default industry median for GP/A filter (updated weekly in production)
_GPA_INDUSTRY_MEDIAN = 0.25


def _synthetic_transaction(ticker: str) -> InsiderTransaction:
    """Placeholder size-1 transaction for single-ticker investigation mode."""
    from datetime import date

    today = date.today()
    return InsiderTransaction(
        cik=f"SYNTHETIC-{ticker}",
        name="(single-ticker mode placeholder)",
        title="N/A",
        ticker=ticker,
        shares=0.0,
        price=0.0,
        value_usd=0.0,
        transaction_date=today,
        filed_date=today,
        transaction_code="P",
        is_director=False,
        is_officer=False,
        is_ten_percent_owner=False,
    )


# ---------------------------------------------------------------------------
# Audit helper — wraps a coroutine node with DuckDB logging
# ---------------------------------------------------------------------------


_NodeFn = Callable[..., Coroutine[Any, Any, dict[str, Any]]]


def _audited(
    db_path_getter: Callable[[], str],
) -> Callable[[_NodeFn], _NodeFn]:
    """
    Decorator factory.  Usage::

        @_audited(lambda: settings.db_path)
        async def my_node(state: PipelineState) -> dict[str, Any]:
            ...
    """

    def decorator(fn: _NodeFn) -> _NodeFn:
        async def wrapper(state: PipelineState) -> dict[str, Any]:
            entered_at = datetime.now(UTC)
            node_name = fn.__name__
            run_id = state.get("run_id", "unknown")
            log.info("node_enter", run_id=run_id, node=node_name)
            try:
                result = await fn(state)
            except Exception as exc:
                exited_at = datetime.now(UTC)
                duration = (exited_at - entered_at).total_seconds() * 1000
                repo.log_node_execution(
                    db_path_getter(),
                    run_id,
                    node_name,
                    entered_at,
                    exited_at,
                    duration,
                    f"ERROR: {exc}",
                )
                log.error("node_error", run_id=run_id, node=node_name, error=str(exc))
                raise
            exited_at = datetime.now(UTC)
            duration = (exited_at - entered_at).total_seconds() * 1000
            summary = str({k: type(v).__name__ for k, v in result.items()})[:200]
            repo.log_node_execution(
                db_path_getter(), run_id, node_name, entered_at, exited_at, duration, summary
            )
            log.info("node_exit", run_id=run_id, node=node_name, duration_ms=duration)
            return result

        wrapper.__name__ = fn.__name__
        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Node factories
# ---------------------------------------------------------------------------


def make_nodes(agents: Any, settings: Any) -> dict[str, Any]:
    """
    Build all node coroutines with agents and settings injected via closure.

    *agents* is an object (or SimpleNamespace) with attributes:
        .regime, .form4, .insider_history, .fundamentals,
        .valuation, .short_interest, .revisions, .liquidity

    Returns a dict mapping node name → coroutine function.
    """
    db_path = settings.db_path
    audit = _audited(lambda: db_path)

    # ------------------------------------------------------------------ regime

    @audit
    async def regime_check(state: PipelineState) -> dict[str, Any]:
        regime = await agents.regime.fetch()
        if regime.regime == "risk-off" and not state.get("force", False):
            raise QivcDataError(
                "Market regime is risk-off. Pass --force to override the entry block."
            )
        return {"regime": regime}

    # ------------------------------------------------------------------ ingest

    @audit
    async def ingest_form4(state: PipelineState) -> dict[str, Any]:
        tickers: list[str] = state.get("tickers", [])
        lookback_days: int = state.get("lookback_days", 14)
        single_ticker_mode: bool = state.get("single_ticker_mode", False)

        if single_ticker_mode:
            # Skip Form 4 ingest: synthesise one placeholder transaction per ticker
            # so downstream fetch nodes process it and a size-1 cluster is created.
            txns_by_ticker = {t: [_synthetic_transaction(t)] for t in tickers}
        elif tickers:
            # Explicit ticker list: fetch per-ticker
            all_txns: list[list[InsiderTransaction]] = await asyncio.gather(
                *[agents.form4.fetch(ticker=t, lookback_days=lookback_days) for t in tickers]
            )
            txns_by_ticker = {}
            for ticker, txns in zip(tickers, all_txns, strict=False):
                if txns:
                    txns_by_ticker[ticker] = txns
        else:
            # Global scan: discover from recent Form 4 P-code purchases
            txns_flat: list[InsiderTransaction] = await agents.form4.fetch(
                lookback_days=lookback_days
            )
            txns_by_ticker = {}
            for t in txns_flat:
                txns_by_ticker.setdefault(t.ticker, []).append(t)

        return {"transactions_by_ticker": txns_by_ticker}

    # --------------------------------------------------------- parallel fetches

    @audit
    async def fetch_insider_histories(state: PipelineState) -> dict[str, Any]:
        # All unique CIKs across all ticker transactions
        all_txns = [t for txns in state["transactions_by_ticker"].values() for t in txns]
        unique_ciks = {t.cik for t in all_txns}
        years = settings.cmp_history_years

        histories_raw = await asyncio.gather(
            *[agents.insider_history.fetch(cik=cik, years=years) for cik in unique_ciks],
            return_exceptions=True,
        )
        histories: dict[str, InsiderHistory] = {}
        for cik, result in zip(unique_ciks, histories_raw, strict=False):
            if isinstance(result, BaseException):
                log.warning("insider_history_failed", cik=cik, error=str(result))
            else:
                histories[cik] = result
        return {"insider_histories": histories}

    @audit
    async def fetch_fundamentals(state: PipelineState) -> dict[str, Any]:
        tickers = list(state["transactions_by_ticker"].keys())
        results = await asyncio.gather(
            *[agents.fundamentals.fetch(ticker=t) for t in tickers],
            return_exceptions=True,
        )
        out: dict[str, Fundamentals] = {}
        for ticker, r in zip(tickers, results, strict=False):
            if isinstance(r, BaseException):
                log.warning("fundamentals_failed", ticker=ticker, error=str(r))
            else:
                out[ticker] = r
        return {"fundamentals": out}

    @audit
    async def fetch_valuation(state: PipelineState) -> dict[str, Any]:
        tickers = list(state["transactions_by_ticker"].keys())
        results = await asyncio.gather(
            *[agents.valuation.fetch(ticker=t) for t in tickers],
            return_exceptions=True,
        )
        out: dict[str, Valuation] = {}
        for ticker, r in zip(tickers, results, strict=False):
            if isinstance(r, BaseException):
                log.warning("valuation_failed", ticker=ticker, error=str(r))
            else:
                out[ticker] = r
        return {"valuations": out}

    @audit
    async def fetch_short_interest(state: PipelineState) -> dict[str, Any]:
        tickers = list(state["transactions_by_ticker"].keys())
        results = await asyncio.gather(
            *[agents.short_interest.fetch(ticker=t) for t in tickers],
            return_exceptions=True,
        )
        out: dict[str, ShortInterest] = {}
        for ticker, r in zip(tickers, results, strict=False):
            if isinstance(r, BaseException):
                log.warning("short_interest_failed", ticker=ticker, error=str(r))
            else:
                out[ticker] = r
        return {"short_interest": out}

    @audit
    async def fetch_revisions(state: PipelineState) -> dict[str, Any]:
        tickers = list(state["transactions_by_ticker"].keys())
        results = await asyncio.gather(
            *[agents.revisions.fetch(ticker=t) for t in tickers],
            return_exceptions=True,
        )
        out: dict[str, EpsRevisions] = {}
        for ticker, r in zip(tickers, results, strict=False):
            if isinstance(r, BaseException):
                log.warning("revisions_failed", ticker=ticker, error=str(r))
            else:
                out[ticker] = r
        return {"revisions": out}

    @audit
    async def fetch_liquidity(state: PipelineState) -> dict[str, Any]:
        tickers = list(state["transactions_by_ticker"].keys())
        results = await asyncio.gather(
            *[agents.liquidity.fetch(ticker=t) for t in tickers],
            return_exceptions=True,
        )
        out: dict[str, Liquidity] = {}
        for ticker, r in zip(tickers, results, strict=False):
            if isinstance(r, BaseException):
                log.warning("liquidity_failed", ticker=ticker, error=str(r))
            else:
                out[ticker] = r
        return {"liquidity": out}

    # --------------------------------------------------------- cluster detection

    @audit
    async def apply_cluster_detection(state: PipelineState) -> dict[str, Any]:
        txns_by_ticker = state["transactions_by_ticker"]

        # Single-ticker mode: synthesise one size-1 cluster per ticker so the
        # remaining quality/valuation/liquidity gates can run on the ticker.
        if state.get("single_ticker_mode", False):
            clusters = [
                Cluster(
                    ticker=ticker,
                    transactions=txns,
                    track="B",
                    window_start=txns[0].transaction_date,
                    window_end=txns[0].transaction_date,
                    total_value_usd=sum(t.value_usd for t in txns),
                )
                for ticker, txns in txns_by_ticker.items()
                if txns
            ]
            return {"clusters": clusters}

        histories = state.get("insider_histories", {})

        # Classify every transaction by CIK using CMP
        all_txns = [t for txns in txns_by_ticker.values() for t in txns]
        classifications: dict[str, str] = {}
        for txn in all_txns:
            cik = txn.cik
            if cik not in classifications:
                h = histories.get(cik)
                if h is None:
                    classifications[cik] = "unclassified"
                else:
                    classifications[cik] = cmp_mod.classify(txn, h)

        # De-dup co-filer fan-out for cluster counting ONLY: one filing is one
        # event, not N signals (see cluster_detector.dedupe_by_accession and
        # STRATEGY_NOTES "Fan-Out Dedup Policy"). The classifications table below
        # still consumes the full fan-out so audit sees every listed insider.
        cluster_input = cluster_mod.dedupe_by_accession(all_txns)
        clusters = cluster_mod.detect_clusters(
            cluster_input,
            classifications,
            window_days=settings.cluster_window_days,
        )

        # Persist per-insider classifications for audit (one row per ticker-CIK).
        run_id = state.get("run_id", "unknown")
        agg: dict[tuple[str, str], dict[str, Any]] = {}
        for t in all_txns:
            key = (t.ticker, t.cik)
            rec = agg.get(key)
            if rec is None:
                hist = histories.get(t.cik)
                agg[key] = {
                    "ticker": t.ticker,
                    "cik": t.cik,
                    "name": t.name,
                    "classification": classifications.get(t.cik, "unclassified"),
                    "years_history": hist.years_of_history if hist else 0,
                    "n_purchases": 1,
                    "total_value_usd": t.value_usd,
                    "is_officer": t.is_officer,
                }
            else:
                rec["n_purchases"] = int(rec["n_purchases"]) + 1
                rec["total_value_usd"] = float(rec["total_value_usd"]) + t.value_usd
                rec["is_officer"] = bool(rec["is_officer"]) or t.is_officer
        repo.save_insider_classifications(db_path, run_id, list(agg.values()))

        return {"clusters": clusters}

    # --------------------------------------------------------------- filters

    @audit
    async def apply_filters(state: PipelineState) -> dict[str, Any]:
        candidates: list[Candidate] = []
        rejected: list[RejectedCandidate] = []
        all_filter_results: dict[str, list[FilterResult]] = {}

        # Build a lookup of clusters by ticker
        clusters_by_ticker: dict[str, list[Cluster]] = {}
        for c in state.get("clusters", []):
            clusters_by_ticker.setdefault(c.ticker, []).append(c)

        regime = state.get("regime")

        for ticker in state["transactions_by_ticker"]:
            results: list[FilterResult] = []

            # -- regime gate --
            if regime is not None:
                r_regime = regime_mod.apply(regime)
                results.append(r_regime)
                if r_regime.passed is False:
                    rejected.append(
                        RejectedCandidate(
                            ticker=ticker,
                            failed_gate="regime",
                            reason=r_regime.reason,
                            filter_results=results,
                        )
                    )
                    all_filter_results[ticker] = results
                    continue

            # -- insider conviction gate (cluster detection output) --
            ticker_clusters = clusters_by_ticker.get(ticker, [])
            conviction_result = FilterResult(
                filter_name="insider_conviction",
                passed=len(ticker_clusters) > 0,
                metric_value=float(len(ticker_clusters)),
                threshold=1.0,
                reason=(
                    f"{len(ticker_clusters)} cluster(s) detected"
                    if ticker_clusters
                    else "No Track A or Track B cluster detected"
                ),
            )
            results.append(conviction_result)
            if not conviction_result.passed:
                rejected.append(
                    RejectedCandidate(
                        ticker=ticker,
                        failed_gate="insider_conviction",
                        reason=conviction_result.reason,
                        filter_results=results,
                    )
                )
                all_filter_results[ticker] = results
                continue

            # -- check data availability for remaining filters --
            missing: list[str] = []
            if ticker not in state.get("fundamentals", {}):
                missing.append("fundamentals")
            if ticker not in state.get("valuations", {}):
                missing.append("valuations")
            if ticker not in state.get("short_interest", {}):
                missing.append("short_interest")
            if ticker not in state.get("revisions", {}):
                missing.append("revisions")
            if ticker not in state.get("liquidity", {}):
                missing.append("liquidity")
            if missing:
                rejected.append(
                    RejectedCandidate(
                        ticker=ticker,
                        failed_gate=missing[0],
                        reason=f"STATUS: UNVERIFIABLE — missing data: {missing}",
                        filter_results=results,
                    )
                )
                all_filter_results[ticker] = results
                continue

            ci = CandidateInput(
                ticker=ticker,
                fundamentals=state["fundamentals"][ticker],
                valuation=state["valuations"][ticker],
                short_interest=state["short_interest"][ticker],
                eps_revisions=state["revisions"][ticker],
                liquidity=state["liquidity"][ticker],
                transactions=ticker_clusters[0].transactions,
            )

            # -- sequential filter gates --
            gate_failed: str | None = None
            gate_results: list[FilterResult] = [
                fscore_mod.apply(ci),
                gpa_mod.apply(ci, _GPA_INDUSTRY_MEDIAN),
                valuation_mod.apply(ci),
                revisions_mod.apply(ci),
                si_mod.apply(ci),
                liquidity_mod.apply(ci, _TARGET_POSITION_USD),
            ]
            for fr in gate_results:
                results.append(fr)
                if fr.passed is False and gate_failed is None:
                    gate_failed = fr.filter_name

            all_filter_results[ticker] = results

            if gate_failed:
                failed_fr = next(r for r in results if r.filter_name == gate_failed)
                rejected.append(
                    RejectedCandidate(
                        ticker=ticker,
                        failed_gate=gate_failed,
                        reason=failed_fr.reason,
                        filter_results=results,
                    )
                )
            else:
                candidates.append(
                    Candidate(
                        ticker=ticker,
                        cluster=ticker_clusters[0],
                        filter_results=results,
                        sector=ci.valuation.sector,
                        gics_industry_group=ci.valuation.gics_industry,
                    )
                )

        return {
            "candidates": candidates,
            "rejected": rejected,
            "filter_results": all_filter_results,
        }

    # --------------------------------------------------------- synthesis (placeholder)

    @audit
    async def apply_synthesis(state: PipelineState) -> dict[str, Any]:
        """
        Score each surviving candidate, apply the regime cap, then the
        sector and sub-sector caps; return the updated candidate list.
        """
        candidates = state.get("candidates", [])
        regime = state.get("regime")
        valuations = state.get("valuations", {})

        scored: list[Candidate] = []
        for cand in candidates:
            # F-Score from the recorded filter result
            fscore = 0
            for fr in cand.filter_results:
                if fr.filter_name == "f_score" and fr.metric_value is not None:
                    fscore = int(fr.metric_value)
                    break

            # Cluster intensity from the cluster composition
            cluster = cand.cluster
            distinct = len({t.cik for t in cluster.transactions})
            titles = " ".join(t.title.upper() for t in cluster.transactions)
            has_csuite = any(t.is_officer for t in cluster.transactions)
            intensity = ClusterIntensity(
                distinct_insiders=distinct,
                has_csuite=has_csuite,
                has_ceo="CEO" in titles or "CHIEF EXECUTIVE" in titles,
                has_cfo="CFO" in titles or "CHIEF FINANCIAL" in titles,
            )

            # Valuation discount fraction below the sector median
            discount = 0.0
            val = valuations.get(cand.ticker)
            if val is not None and val.sector_median_metric_value:
                metric = (
                    val.forward_pe
                    if val.forward_pe is not None
                    else val.ev_ebitda
                    if val.ev_ebitda is not None
                    else val.p_tbv
                )
                if metric is not None and val.sector_median_metric_value > 0:
                    discount = max(
                        0.0,
                        (val.sector_median_metric_value - metric) / val.sector_median_metric_value,
                    )

            # Insider track record is not yet computed in the live pipeline →
            # None (0 points), per the brief's UNCLASSIFIED fallback.
            cs = conviction_mod.score(
                fscore=fscore,
                insider_track_record=None,
                valuation_discount_pct=discount,
                cluster_intensity=intensity,
            )

            base_size = cs.indicative_size
            flags = list(cand.flags)
            if regime is not None:
                base_size, note = overlay_mod.apply_regime_cap(base_size, regime)
                flags.append(note)

            scored.append(
                cand.model_copy(
                    update={
                        "conviction_score": cs.total,
                        "conviction_breakdown": cs.breakdown,
                        "indicative_size_pct": base_size,
                        "flags": flags,
                    }
                )
            )

        # Sector cap, then sub-sector cap
        scored = overlay_mod.apply_sector_cap(scored)
        scored = overlay_mod.apply_subsector_cap(scored)

        return {"candidates": scored}

    return {
        "regime_check": regime_check,
        "ingest_form4": ingest_form4,
        "fetch_insider_histories": fetch_insider_histories,
        "fetch_fundamentals": fetch_fundamentals,
        "fetch_valuation": fetch_valuation,
        "fetch_short_interest": fetch_short_interest,
        "fetch_revisions": fetch_revisions,
        "fetch_liquidity": fetch_liquidity,
        "apply_cluster_detection": apply_cluster_detection,
        "apply_filters": apply_filters,
        "apply_synthesis": apply_synthesis,
    }
