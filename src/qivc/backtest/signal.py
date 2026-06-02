"""
Full QIVC v2.0 signal, evaluated point-in-time for the backtest (Task 10).

``qivc_signal_as_of`` mirrors the live screen (regime -> insider clustering ->
quality/value/liquidity gates -> conviction sizing -> sector caps) but every
input is sourced as of a 2025 rebalance date with no look-ahead:

  - Insider clusters + CMP history come from the local bulk store
    (form4_historical), filtered strictly to filings filed BEFORE as_of.
  - Fundamentals (F-Score, GP/A) come from the most recent 10-K/10-Q filed
    before as_of (injected provider, PIT).
  - Liquidity comes from price/volume up to as_of (injected provider, PIT).
  - Regime is the PIT FRED regime (injected).

Gates with no free point-in-time data source are returned UNVERIFIABLE (they do
NOT reject and do NOT use current values — avoiding look-ahead):
  - valuation: no PIT sector medians exist (the live system has none either —
    refresh_sector_medians is a stub), so sector_median is None -> UNVERIFIABLE.
  - revisions: no PIT consensus-EPS snapshots -> None deltas -> UNVERIFIABLE.
  - short_interest: no PIT short-interest history -> injected UNVERIFIABLE row.

So the effective binding gates in the backtest are: regime, insider_conviction,
F-Score, GP/A, liquidity. This is documented in BACKTEST_LIMITATIONS.md.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from dataclasses import dataclass, field

from qivc.backtest.pit import filter_by_filing_date
from qivc.data import bulk_loader
from qivc.filters import fscore as fscore_mod
from qivc.filters import gpa as gpa_mod
from qivc.filters import liquidity as liquidity_mod
from qivc.filters import revisions as revisions_mod
from qivc.filters import valuation as valuation_mod
from qivc.filters.cluster_detector import dedupe_by_accession, detect_clusters
from qivc.filters.insider_classifier import classify, compute_years_of_history
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
    MarketRegime,
    ShortInterest,
    Valuation,
)
from qivc.synthesis import conviction as conviction_mod
from qivc.synthesis import risk_overlay as overlay_mod

_GPA_INDUSTRY_MEDIAN = 0.25
_TARGET_POSITION_USD = 1_000_000.0
_FSCORE_THRESHOLD = 7

# Providers fetch PIT data for a single ticker as of a date (None = unavailable).
FundamentalsProvider = Callable[[str, _dt.date], Fundamentals | None]
LiquidityProvider = Callable[[str, _dt.date], Liquidity | None]
IndustryProvider = Callable[[str], str]


@dataclass(frozen=True)
class WeightedHolding:
    ticker: str
    weight: float  # fraction of portfolio
    conviction_score: int
    sector: str
    gics_industry_group: str
    track: str  # cluster track A/B
    n_insiders: int
    flags: tuple[str, ...]


@dataclass(frozen=True)
class SignalResult:
    as_of: _dt.date
    regime: MarketRegime
    holdings: list[WeightedHolding]
    candidates: list[Candidate]
    rejected_count: int
    rejected_by_gate: dict[str, int]
    cash_weight: float
    n_clustered_tickers: int
    regime_blocked: bool
    diagnostics: dict[str, object] = field(default_factory=dict)


def _cluster_intensity(cluster: Cluster) -> ClusterIntensity:
    txns = cluster.transactions
    distinct = len({t.cik for t in txns})
    officers = [t for t in txns if t.is_officer]
    has_ceo = any("ceo" in t.title.lower() or "chief executive" in t.title.lower() for t in txns)
    has_cfo = any("cfo" in t.title.lower() or "chief financial" in t.title.lower() for t in txns)
    return ClusterIntensity(
        distinct_insiders=distinct,
        has_csuite=bool(officers),
        has_ceo=has_ceo,
        has_cfo=has_cfo,
    )


def _classify_window(
    db_path: str, txns: list[InsiderTransaction], as_of: _dt.date, cmp_history_years: int
) -> dict[str, str]:
    """CMP-classify each transaction's CIK using bulk history strictly before as_of."""
    classifications: dict[str, str] = {}
    window_start = _dt.date(as_of.year - cmp_history_years, 1, 1)
    for txn in txns:
        cik = txn.cik
        if cik in classifications:
            continue
        history = bulk_loader.read_purchases_for_cik(db_path, cik, window_start, as_of)
        history = filter_by_filing_date(history, as_of)  # strict PIT
        years = compute_years_of_history(history, as_of)
        classifications[cik] = classify(
            txn, InsiderHistory(cik=cik, transactions=history, years_of_history=years)
        )
    return classifications


def _unverifiable_short_interest(ticker: str, as_of: _dt.date) -> ShortInterest:
    """Neutral placeholder; the SI gate row is forced UNVERIFIABLE downstream."""
    return ShortInterest(
        ticker=ticker,
        si_pct_float=0.0,
        prior_si_pct_float=0.0,
        report_date=as_of,
        direction="flat",
    )


def _unverifiable_revisions(ticker: str) -> EpsRevisions:
    """All-None deltas -> the revisions gate returns UNVERIFIABLE (no PIT data)."""
    return EpsRevisions(
        ticker=ticker, delta_30d=None, delta_60d=None, delta_90d=None, accelerating=False
    )


def clusters_as_of(
    as_of: _dt.date,
    *,
    db_path: str,
    universe: set[str],
    lookback_days: int = 14,
    cmp_history_years: int = 3,
    cluster_window_days: int = 7,
    track_b_min_usd: float = 250_000.0,
    track_a_min_distinct: int = 3,
) -> dict[str, Cluster]:
    """
    Detect insider clusters as of ``as_of`` from the bulk store only (no EDGAR/
    yfinance). Strictly PIT (filings filed before as_of), universe-restricted.
    Used both by the full signal and by the backtest's cheap pass-1 (to discover
    which tickers ever cluster, so expensive PIT fetches are bounded to those).
    """
    window_start = as_of - _dt.timedelta(days=lookback_days)
    txns = bulk_loader.read_purchases(db_path, window_start, as_of)
    txns = filter_by_filing_date(txns, as_of)
    txns = [t for t in txns if t.ticker in universe]
    cluster_input = dedupe_by_accession(txns)
    classifications = _classify_window(db_path, cluster_input, as_of, cmp_history_years)
    clusters = detect_clusters(
        cluster_input,
        classifications,
        window_days=cluster_window_days,
        track_b_min_usd=track_b_min_usd,
        track_a_min_distinct=track_a_min_distinct,
    )
    return {c.ticker: c for c in clusters}


def qivc_signal_as_of(
    as_of: _dt.date,
    *,
    db_path: str,
    universe: set[str],
    sector_map: dict[str, str],
    regime: MarketRegime,
    fundamentals_provider: FundamentalsProvider,
    liquidity_provider: LiquidityProvider,
    industry_provider: IndustryProvider,
    lookback_days: int = 14,
    cmp_history_years: int = 3,
    cluster_window_days: int = 7,
    track_b_min_usd: float = 250_000.0,
    track_a_min_distinct: int = 3,
    fscore_threshold: int = _FSCORE_THRESHOLD,
    gpa_median: float = _GPA_INDUSTRY_MEDIAN,
    force: bool = False,
) -> SignalResult:
    """Evaluate the full QIVC signal as of ``as_of`` (no look-ahead)."""
    diagnostics: dict[str, object] = {}

    # ---- Gate 0: regime (entries blocked when risk-off) ----
    if regime.regime == "risk-off" and not force:
        return SignalResult(
            as_of=as_of,
            regime=regime,
            holdings=[],
            candidates=[],
            rejected_count=0,
            rejected_by_gate={},
            cash_weight=1.0,
            n_clustered_tickers=0,
            regime_blocked=True,
            diagnostics={"regime_block": True},
        )

    # ---- Insider clustering (bulk store, strictly PIT, restricted to universe) ----
    clusters_by_ticker = clusters_as_of(
        as_of,
        db_path=db_path,
        universe=universe,
        lookback_days=lookback_days,
        cmp_history_years=cmp_history_years,
        cluster_window_days=cluster_window_days,
        track_b_min_usd=track_b_min_usd,
        track_a_min_distinct=track_a_min_distinct,
    )
    diagnostics["n_clustered_tickers"] = len(clusters_by_ticker)

    # ---- Quality / value / liquidity gates per clustered ticker ----
    candidates: list[Candidate] = []
    rejected_by_gate: dict[str, int] = {}
    rejected_count = 0

    for ticker, cluster in sorted(clusters_by_ticker.items()):
        fundamentals = fundamentals_provider(ticker, as_of)
        liquidity = liquidity_provider(ticker, as_of)
        if fundamentals is None or liquidity is None:
            gate = "fundamentals" if fundamentals is None else "liquidity"
            rejected_by_gate[gate + "_missing"] = rejected_by_gate.get(gate + "_missing", 0) + 1
            rejected_count += 1
            continue

        valuation = Valuation(
            ticker=ticker,
            sector=sector_map.get(ticker, "Unknown"),
            gics_industry=industry_provider(ticker),
            forward_pe=None,
            ev_ebitda=None,
            p_tbv=None,
            p_affo=None,
            sector_median_metric_value=None,  # no PIT median -> UNVERIFIABLE
        )
        ci = CandidateInput(
            ticker=ticker,
            fundamentals=fundamentals,
            valuation=valuation,
            short_interest=_unverifiable_short_interest(ticker, as_of),
            eps_revisions=_unverifiable_revisions(ticker),
            liquidity=liquidity,
            transactions=cluster.transactions,
        )

        results = [
            fscore_mod.apply(ci, threshold=fscore_threshold),
            gpa_mod.apply(ci, gpa_median),
            valuation_mod.apply(ci),  # UNVERIFIABLE (no sector median)
            revisions_mod.apply(ci),  # UNVERIFIABLE (None deltas)
            _unverifiable_si_result(),  # injected UNVERIFIABLE (no PIT SI data)
            liquidity_mod.apply(ci, _TARGET_POSITION_USD),
        ]
        gate_failed = next((r.filter_name for r in results if r.passed is False), None)
        if gate_failed:
            rejected_by_gate[gate_failed] = rejected_by_gate.get(gate_failed, 0) + 1
            rejected_count += 1
            continue

        candidates.append(
            Candidate(
                ticker=ticker,
                cluster=cluster,
                filter_results=results,
                sector=valuation.sector,
                gics_industry_group=valuation.gics_industry,
            )
        )

    # ---- Conviction sizing + regime/sector/subsector caps ----
    scored = _score_and_size(candidates, clusters_by_ticker, regime)
    scored = overlay_mod.apply_sector_cap(scored)
    scored = overlay_mod.apply_subsector_cap(scored)

    holdings = [
        WeightedHolding(
            ticker=c.ticker,
            weight=c.indicative_size_pct,
            conviction_score=c.conviction_score,
            sector=c.sector,
            gics_industry_group=c.gics_industry_group,
            track=clusters_by_ticker[c.ticker].track,
            n_insiders=len({t.cik for t in clusters_by_ticker[c.ticker].transactions}),
            flags=tuple(c.flags),
        )
        for c in scored
    ]
    total_w = sum(h.weight for h in holdings)
    if total_w > 1.0:  # scale to fully-invested; no leverage
        holdings = [
            WeightedHolding(**{**h.__dict__, "weight": h.weight / total_w}) for h in holdings
        ]
        total_w = 1.0
    cash_weight = max(0.0, 1.0 - total_w)

    return SignalResult(
        as_of=as_of,
        regime=regime,
        holdings=holdings,
        candidates=scored,
        rejected_count=rejected_count,
        rejected_by_gate=rejected_by_gate,
        cash_weight=cash_weight,
        n_clustered_tickers=len(clusters_by_ticker),
        regime_blocked=False,
        diagnostics=diagnostics,
    )


def _unverifiable_si_result() -> FilterResult:
    return FilterResult(
        filter_name="short_interest",
        passed=None,
        metric_value=None,
        threshold=None,
        reason="UNVERIFIABLE — no point-in-time short-interest data for backtest",
    )


def _score_and_size(
    candidates: list[Candidate],
    clusters_by_ticker: dict[str, Cluster],
    regime: MarketRegime,
) -> list[Candidate]:
    """Conviction-score each candidate, set indicative_size_pct (after regime cap)."""
    out: list[Candidate] = []
    for c in candidates:
        fscore_fr = next((r for r in c.filter_results if r.filter_name == "f_score"), None)
        fscore_val = int(fscore_fr.metric_value) if fscore_fr and fscore_fr.metric_value else 0
        intensity = _cluster_intensity(clusters_by_ticker[c.ticker])
        cs = conviction_mod.score(
            fscore=fscore_val,
            insider_track_record=None,  # not available (no positive-12m history wiring)
            valuation_discount_pct=0.0,  # no PIT sector median -> no discount credit
            cluster_intensity=intensity,
        )
        sized, _ = overlay_mod.apply_regime_cap(cs.indicative_size, regime)
        out.append(
            c.model_copy(
                update={
                    "conviction_score": cs.total,
                    "conviction_breakdown": cs.breakdown,
                    "indicative_size_pct": sized,
                }
            )
        )
    return out
