"""ShortInterestAgent — SI% of float and direction via yfinance."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import yfinance as yf

from qivc.data import DataAgent
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError
from qivc.schemas import ShortInterest

log = logging.getLogger(__name__)

_DIRECTION_THRESHOLD = 0.005  # 0.5 percentage-point band for "flat"


def _compute_direction(current: float, prior: float) -> str:
    diff = current - prior
    if diff > _DIRECTION_THRESHOLD:
        return "rising"
    if diff < -_DIRECTION_THRESHOLD:
        return "falling"
    return "flat"


class ShortInterestAgent(DataAgent[ShortInterest]):
    """Computes short-interest % of float and direction (rising/flat/falling)."""

    def __init__(self, client: EdgarClient) -> None:
        self._client = client

    @property
    def source_name(self) -> str:
        return "yfinance_short_interest"

    async def fetch(self, **kwargs: Any) -> ShortInterest:
        """
        Keyword args:
            ticker (str): company ticker symbol
        """
        ticker: str = str(kwargs["ticker"])

        loop = asyncio.get_running_loop()
        try:
            info: dict[str, Any] = await loop.run_in_executor(None, lambda: yf.Ticker(ticker).info)
        except Exception as exc:
            raise QivcDataError(
                f"ShortInterestAgent yfinance.info failed for {ticker}: {exc}"
            ) from exc

        # Current SI% of float (expressed as a decimal; e.g. 0.05 = 5%)
        si_raw: Any = info.get("shortPercentOfFloat")
        try:
            si_pct: float = float(si_raw) if si_raw is not None else 0.0
        except (TypeError, ValueError):
            si_pct = 0.0

        # Prior-month SI%: shares_short_prior_month / shares_outstanding
        shares_outstanding: Any = info.get("sharesOutstanding")
        shares_short_prior: Any = info.get("sharesShortPriorMonth")
        try:
            so = float(shares_outstanding) if shares_outstanding is not None else 0.0
            sp = float(shares_short_prior) if shares_short_prior is not None else 0.0
            prior_pct: float = sp / so if so > 0 else si_pct  # fallback to current
        except (TypeError, ValueError):
            prior_pct = si_pct

        report_date_raw: Any = info.get("sharesShortPreviousMonthDate")
        if report_date_raw:
            try:
                report_dt = date.fromtimestamp(int(report_date_raw))
            except (TypeError, ValueError, OSError):
                report_dt = date.today()
        else:
            report_dt = date.today()

        return ShortInterest(
            ticker=ticker,
            si_pct_float=si_pct,
            prior_si_pct_float=prior_pct,
            report_date=report_dt,
            direction=_compute_direction(si_pct, prior_pct),
        )
