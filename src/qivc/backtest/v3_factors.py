"""
v3.0 point-in-time factor assembly (Phase 2).

At a rebalance date, assemble ``StockFactors`` for the **scored universe**, which
— for feasibility and strategy coherence — is the **insider-active subset** of
IWM-ex-Financials/REITs: names with >=1 *opportunistic* (CMP) open-market
purchase filed in the trailing 90 days. Rationale:

  - The insider signal is the 40% primary factor; a name with no insider activity
    scores ~0 on it and effectively cannot reach top-N, so excluding zero-insider
    names from scoring does not change selection materially.
  - Scoring all ~1,400 universe names would require PIT fundamentals for each,
    every month — tens of thousands of live EDGAR fetches. Infeasible.

This is documented as a scoping deviation from the literal "rank the whole
universe" spec (BACKTEST_REVIEW_V3_GRID.md will surface it).

Factor data sources (all strictly PIT, no look-ahead):
  - **insider** (40%): bulk ``form4_historical``, recency- & conviction-weighted
    opportunistic P-buys in the trailing 90 days.
  - **quality** (30%): F-Score + GP/A from EDGAR 10-K/10-Q filed before as_of
    (injected provider), GP/A ranked within sector by the scorer.
  - **valuation** (20%) and **momentum** (10%): **None → neutral.** No PIT
    sector-median pipeline exists (OQ-5) and no free PIT EPS-revision snapshots,
    so these run neutral rather than introducing current-data look-ahead into a
    4-year cross-validation. The framework keeps the slots; they activate once
    OQ-5's pipeline and a PIT revisions source exist.
"""

from __future__ import annotations

import datetime as _dt
import math
from collections.abc import Callable

from qivc.backtest.composite_scorer import StockFactors
from qivc.backtest.pit import filter_by_filing_date
from qivc.backtest.signal import _classify_window
from qivc.data import bulk_loader
from qivc.filters.cluster_detector import dedupe_by_accession
from qivc.schemas import Fundamentals, InsiderTransaction

INSIDER_LOOKBACK_DAYS = 90
_OPPORTUNISTIC = "opportunistic"

FundamentalsProvider = Callable[[str, _dt.date], Fundamentals | None]
TechnicalProvider = Callable[[str, _dt.date], float | None]


def _insider_weight(
    txn: InsiderTransaction, as_of: _dt.date, lookback_days: int = INSIDER_LOOKBACK_DAYS
) -> float:
    """Recency- and conviction-weighted contribution of one opportunistic buy."""
    days_ago = (as_of - txn.filed_date).days
    recency = max(0.0, 1.0 - days_ago / lookback_days)  # recent -> ~1, old -> ~0
    csuite = 1.5 if txn.is_officer else 1.0
    size = min(max(txn.value_usd, 0.0) / 250_000.0, 4.0)  # capped size influence
    return recency * csuite * math.log1p(size)


def opportunistic_insider_raw(
    db_path: str,
    universe: set[str],
    as_of: _dt.date,
    cmp_history_years: int = 3,
    lookback_days: int = INSIDER_LOOKBACK_DAYS,
) -> dict[str, float]:
    """
    Recency/conviction-weighted opportunistic-insider activity per ticker over the
    trailing *lookback_days* (DEFAULT 90, the v3.0 baseline — do not change). Strictly
    PIT, universe-restricted. Returns ticker -> raw.
    """
    window_start = as_of - _dt.timedelta(days=lookback_days)
    txns = bulk_loader.read_purchases(db_path, window_start, as_of)
    txns = filter_by_filing_date(txns, as_of)
    txns = [t for t in txns if t.ticker in universe]
    txns = dedupe_by_accession(txns)
    classifications = _classify_window(db_path, txns, as_of, cmp_history_years)
    raw: dict[str, float] = {}
    for t in txns:
        if classifications.get(t.cik) != _OPPORTUNISTIC:
            continue
        raw[t.ticker] = raw.get(t.ticker, 0.0) + _insider_weight(t, as_of, lookback_days)
    return raw


def assemble_factors(
    as_of: _dt.date,
    *,
    db_path: str,
    universe: set[str],
    sector_map: dict[str, str],
    fundamentals_provider: FundamentalsProvider,
    technical_provider: TechnicalProvider | None = None,
    cmp_history_years: int = 3,
    insider_lookback_days: int = INSIDER_LOOKBACK_DAYS,
) -> list[StockFactors]:
    """Build StockFactors for the insider-active scored universe at *as_of*.

    *technical_provider* (Task 12) supplies the PIT technical-oversold raw score;
    when None the factor is neutral (back-compat: v3.0 weights it 0 anyway).
    *insider_lookback_days* DEFAULTS to 90 (v3.0 baseline); the v3.0-w14 fork (Task
    17) passes 14 — nothing else differs.
    """
    insider_raw = opportunistic_insider_raw(
        db_path, universe, as_of, cmp_history_years, insider_lookback_days
    )
    factors: list[StockFactors] = []
    for ticker in sorted(insider_raw):
        fund = fundamentals_provider(ticker, as_of)
        fscore: int | None = None
        gpa: float | None = None
        if fund is not None:
            # F-Score is recomputed by the quality scorer's normalization; here we
            # pass the raw Piotroski inputs the scorer needs: F-Score (0-9) and GP/A.
            fscore = _piotroski(fund)
            gpa = (fund.gross_profit / fund.total_assets) if fund.total_assets else None
        technical_raw = technical_provider(ticker, as_of) if technical_provider else None
        factors.append(
            StockFactors(
                ticker=ticker,
                sector=sector_map.get(ticker, "Unknown"),
                insider_raw=insider_raw[ticker],
                fscore=fscore,
                gpa=gpa,
                valuation_raw=None,  # neutral — no PIT sector medians (OQ-5)
                momentum_raw=None,  # neutral — no PIT EPS-revision snapshots
                technical_raw=technical_raw,
            )
        )
    return factors


def _piotroski(f: Fundamentals) -> int:
    """Piotroski F-Score (0-9) from the Fundamentals inputs (same binarisation as the filter)."""
    score = 0
    score += f.roa > 0
    score += f.ocf > 0
    score += f.delta_roa > 0
    score += f.ocf_gt_ni
    score += f.delta_leverage < 0
    score += f.delta_liquidity > 0
    score += f.no_share_issuance
    score += f.delta_gross_margin > 0
    score += f.delta_asset_turnover > 0
    return int(score)
