"""FundamentalsAgent — computes 9 Piotroski raw inputs from XBRL filings."""

from __future__ import annotations

import logging
from typing import Any

from qivc.data import DataAgent
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError
from qivc.schemas import Fundamentals

log = logging.getLogger(__name__)


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerce *val* to float, returning *default* on failure."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _compute_fundamentals(
    ticker: str,
    fiscal_period: str,
    cur: Any,
    prior: Any,
) -> Fundamentals:
    """
    Derive the 9 Piotroski input fields from two Financials objects.
    *cur* is the most-recent period; *prior* is the year-ago period.
    All delta fields are (current - prior) so the filter can apply > 0 threshold.
    """
    # Current period raw values
    cur_ni = _safe_float(cur.get_net_income())
    cur_ta = _safe_float(cur.get_total_assets())
    cur_ocf = _safe_float(cur.get_operating_cash_flow())
    cur_rev = _safe_float(cur.get_revenue())
    cur_gp = _safe_float(cur.get_operating_income())  # proxy for gross profit
    cur_shares = _safe_float(cur.get_shares_outstanding_basic())

    # Balance-sheet items for leverage and liquidity
    cur_ca = _safe_float(cur.get_current_assets()) if hasattr(cur, "get_current_assets") else 0.0
    cur_cl = (
        _safe_float(cur.get_current_liabilities())
        if hasattr(cur, "get_current_liabilities")
        else 0.0
    )
    cur_ltd = (
        _safe_float(cur.get_total_liabilities()) if hasattr(cur, "get_total_liabilities") else 0.0
    )

    cur_roa = cur_ni / cur_ta if cur_ta else 0.0
    cur_gross_margin = (cur_gp / cur_rev) if cur_rev else 0.0
    cur_asset_turnover = cur_rev / cur_ta if cur_ta else 0.0
    cur_leverage = cur_ltd / cur_ta if cur_ta else 0.0
    cur_current_ratio = cur_ca / cur_cl if cur_cl else 0.0

    # Prior period (needed for all delta calculations)
    if prior is not None:
        prior_ni = _safe_float(prior.get_net_income())
        prior_ta = _safe_float(prior.get_total_assets())
        prior_rev = _safe_float(prior.get_revenue())
        prior_gp = _safe_float(prior.get_operating_income())
        prior_shares = _safe_float(prior.get_shares_outstanding_basic())
        prior_cl = (
            _safe_float(prior.get_current_liabilities())
            if hasattr(prior, "get_current_liabilities")
            else 0.0
        )
        prior_ca = (
            _safe_float(prior.get_current_assets()) if hasattr(prior, "get_current_assets") else 0.0
        )
        prior_ltd = (
            _safe_float(prior.get_total_liabilities())
            if hasattr(prior, "get_total_liabilities")
            else 0.0
        )

        prior_roa = prior_ni / prior_ta if prior_ta else 0.0
        prior_gross_margin = (prior_gp / prior_rev) if prior_rev else 0.0
        prior_asset_turnover = prior_rev / prior_ta if prior_ta else 0.0
        prior_leverage = prior_ltd / prior_ta if prior_ta else 0.0
        prior_current_ratio = prior_ca / prior_cl if prior_cl else 0.0
    else:
        prior_roa = 0.0
        prior_gross_margin = 0.0
        prior_asset_turnover = 0.0
        prior_leverage = 0.0
        prior_current_ratio = 0.0
        prior_shares = cur_shares  # assume no change if no prior data

    return Fundamentals(
        ticker=ticker,
        fiscal_period=fiscal_period,
        roa=cur_roa,
        ocf=cur_ocf,
        delta_roa=cur_roa - prior_roa,
        # Accruals: OCF / total_assets > ROA signals cash-backed earnings
        ocf_gt_ni=(cur_ta > 0 and (cur_ocf / cur_ta) > cur_roa),
        delta_leverage=cur_leverage - prior_leverage,
        delta_liquidity=cur_current_ratio - prior_current_ratio,
        no_share_issuance=(cur_shares <= prior_shares),
        delta_gross_margin=cur_gross_margin - prior_gross_margin,
        delta_asset_turnover=cur_asset_turnover - prior_asset_turnover,
        gross_profit=cur_gp,
        total_assets=cur_ta,
    )


class FundamentalsAgent(DataAgent[Fundamentals]):
    """
    Computes Piotroski F-Score inputs from the two most-recent 10-K filings.
    Does NOT compute the F-Score itself — that is the filter layer's job.
    """

    def __init__(self, client: EdgarClient) -> None:
        self._client = client

    @property
    def source_name(self) -> str:
        return "sec_edgar_xbrl_10k"

    async def fetch(self, **kwargs: Any) -> Fundamentals:
        """
        Keyword args:
            ticker (str): company ticker symbol
        """
        ticker: str = str(kwargs["ticker"])

        try:
            financials_list = await self._client.get_financials_10k(ticker, periods=2)
        except Exception as exc:
            raise QivcDataError(f"FundamentalsAgent.fetch failed for {ticker}: {exc}") from exc

        if not financials_list:
            raise QivcDataError(f"No 10-K XBRL data found for {ticker}")

        cur = financials_list[0]
        prior = financials_list[1] if len(financials_list) > 1 else None

        # Derive fiscal period label from the filing if possible
        fiscal_period = "FY_UNKNOWN"
        try:
            fp = getattr(cur, "period_of_report", None) or getattr(cur, "fiscal_year_end", None)
            if fp:
                fiscal_period = str(fp)
        except Exception:
            pass

        try:
            return _compute_fundamentals(ticker, fiscal_period, cur, prior)
        except Exception as exc:
            raise QivcDataError(f"FundamentalsAgent compute failed for {ticker}: {exc}") from exc
