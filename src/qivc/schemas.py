"""Pydantic v2 schemas — canonical data contracts between agents and filters."""

from __future__ import annotations

from datetime import date
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
    delta_roa: float  # ROA(t) − ROA(t−1)
    ocf_gt_ni: bool  # OCF/total_assets > ROA (accruals quality — already binary)
    delta_leverage: float  # Δ long-term-debt / total-assets ratio
    delta_liquidity: float  # Δ current ratio
    no_share_issuance: bool  # shares outstanding did NOT increase YoY
    delta_gross_margin: float  # Δ gross margin ratio
    delta_asset_turnover: float  # Δ revenue / total-assets
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
    value_growth_12m: float  # IVE − IVW 12-month total return
    regime: Literal["risk-on", "risk-mid", "risk-off"]
