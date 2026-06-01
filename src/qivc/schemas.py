"""Pydantic v2 schemas — canonical data contracts between agents and filters."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class InsiderTransaction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cik: str
    name: str
    title: str
    ticker: str
    shares: float
    price: float
    value_usd: float
    transaction_date: date
    filed_date: date
    transaction_code: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool


class InsiderHistory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cik: str
    transactions: list[InsiderTransaction]
    years_of_history: int


class Fundamentals(BaseModel):
    """Raw Piotroski inputs — binary-isation happens in the filter layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    fiscal_period: str  # e.g. "FY2025" or "2025Q4"

    # Raw values (filter will threshold these)
    roa: float  # net income / total assets — current period
    ocf: float  # operating cash flow — current period (absolute $)
    delta_roa: float  # ROA(t) - ROA(t-1)
    ocf_gt_ni: bool  # OCF/total_assets > ROA (accruals quality — already binary)
    delta_leverage: float  # delta long-term-debt / total-assets ratio
    delta_liquidity: float  # delta current ratio
    no_share_issuance: bool  # shares outstanding did NOT increase YoY
    delta_gross_margin: float  # delta gross margin ratio
    delta_asset_turnover: float  # delta revenue / total-assets
    gross_profit: float
    total_assets: float


class Valuation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    sector: str
    gics_industry: str
    forward_pe: float | None
    ev_ebitda: float | None
    p_tbv: float | None  # price-to-tangible-book (or price-to-book as proxy)
    p_affo: float | None  # price-to-AFFO (REITs only)
    sector_median_metric_value: float | None  # median of the sector's primary metric


class ShortInterest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    si_pct_float: float  # current short interest as % of float
    prior_si_pct_float: float  # prior-month report value
    report_date: date
    direction: Literal["rising", "flat", "falling"]


class EpsRevisions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    delta_30d: float | None  # consensus EPS revision over last 30 days
    delta_60d: float | None
    delta_90d: float | None
    accelerating: bool  # True when delta_30d > delta_60d > delta_90d (all non-None)


class Liquidity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    market_cap_usd: float
    adv_20d_usd: float  # average daily $ volume (20 day)
    next_earnings_date: date | None
    has_pending_ma: bool


class MarketRegime(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    vix_60d_sma: float
    credit_spread_bps: float  # BAA10Y in basis points
    yield_curve_bps: float  # T10Y3M in basis points
    value_growth_12m: float  # IVE - IVW 12-month total return
    regime: Literal["risk-on", "risk-mid", "risk-off"]


# ---------------------------------------------------------------------------
# Filter-layer types (added in Phase 2)
# ---------------------------------------------------------------------------


class FilterResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    filter_name: str
    passed: bool | None  # None = UNVERIFIABLE (missing data)
    metric_value: float | None
    threshold: float | None
    reason: str  # human-readable explanation for audit logs


class CandidateInput(BaseModel):
    """Composite input passed to every filter function."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    fundamentals: Fundamentals
    valuation: Valuation
    short_interest: ShortInterest
    eps_revisions: EpsRevisions
    liquidity: Liquidity
    transactions: list[InsiderTransaction]  # the P-code cluster being evaluated


class Cluster(BaseModel):
    """A detected insider cluster (Track A or Track B)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    transactions: list[InsiderTransaction]
    track: Literal["A", "B"]
    window_start: date
    window_end: date
    total_value_usd: float


# ---------------------------------------------------------------------------
# Synthesis-layer types (added in Phase 3)
# ---------------------------------------------------------------------------


class InsiderTrackRecord(BaseModel):
    """Historical buy track record for the cluster's insiders (Phase 4 scoring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prior_buys: int  # number of prior opportunistic buys in the lookback
    positive_12m: bool  # were those prior buys followed by positive 12m returns


class ClusterIntensity(BaseModel):
    """Summary of a cluster's composition, used by the conviction scorer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    distinct_insiders: int
    has_csuite: bool  # at least one officer in the cluster
    has_ceo: bool
    has_cfo: bool


class ConvictionScore(BaseModel):
    """Output of the 4-dimension conviction scorer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    breakdown: dict[str, int]  # per-dimension points
    indicative_size: float  # FRACTION of portfolio, e.g. 0.07 = 7%


class Candidate(BaseModel):
    """A ticker that passed all active gates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    cluster: Cluster
    filter_results: list[FilterResult]
    sector: str = ""  # GICS sector (for sector cap)
    gics_industry_group: str = ""  # GICS industry/sub-sector (for sub-sector cap)
    conviction_score: int = 0  # populated in Phase 4
    conviction_breakdown: dict[str, int] = {}  # per-dimension points
    indicative_size_pct: float = 0.0  # FRACTION of portfolio (0.07 = 7%); post-overlay
    flags: list[str] = []  # e.g. "EXCEEDS_SECTOR_CAP", regime notes


class RejectedCandidate(BaseModel):
    """A ticker that failed at least one gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    failed_gate: str  # name of the filter that failed
    reason: str
    filter_results: list[FilterResult]


class RunReport(BaseModel):
    """Machine-readable mirror of the Markdown dossier (Phase 5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    generated_at: datetime
    regime: MarketRegime
    candidates: list[Candidate]
    rejected: list[RejectedCandidate]
    universe_size: int  # total tickers screened
    run_duration_seconds: float


# ---------------------------------------------------------------------------
# Backtest types (Phase 7)
# ---------------------------------------------------------------------------


class BacktestTrade(BaseModel):
    """A single round-trip (or still-open) position in a backtest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    entry_date: date
    exit_date: date | None  # None = still held at backtest end
    entry_price: float
    exit_price: float | None
    return_pct: float | None  # fractional return over the hold (None if open)
    hold_days: int | None


class BacktestMetrics(BaseModel):
    """Headline performance metrics for a backtest run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float  # negative fraction, e.g. -0.18
    calmar: float
    hit_rate: float  # fraction of closed trades that were profitable
    avg_hold_days: float
    alpha_vs_iwn: float  # annualised excess return vs Russell 2000 Value (IWN)
    total_return: float
    final_equity: float


class BacktestResult(BaseModel):
    """Full output of a backtest run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backtest_id: str
    start: date
    end: date
    rebalance_freq: Literal["W", "M"]
    initial_capital: float
    slippage_bps: float
    commission_bps: float
    metrics: BacktestMetrics
    equity_curve: list[tuple[date, float]]  # (date, portfolio value)
    trades: list[BacktestTrade]
    notes: list[str]  # methodology caveats (e.g. sector-median lookahead)
