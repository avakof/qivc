"""EstimatesAgent — EPS consensus revision deltas via yfinance."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import yfinance as yf

from qivc.data import DataAgent
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError
from qivc.schemas import EpsRevisions

log = logging.getLogger(__name__)


class EstimatesAgent(DataAgent[EpsRevisions]):
    """
    Fetches EPS consensus revision deltas. yfinance does not expose historical
    estimate snapshots, so revision deltas are returned as None (UNVERIFIABLE)
    unless yfinance provides trend data in the info dict.
    """

    def __init__(self, client: EdgarClient) -> None:
        self._client = client

    @property
    def source_name(self) -> str:
        return "yfinance_eps_revisions"

    async def fetch(self, **kwargs: Any) -> EpsRevisions:
        """
        Keyword args:
            ticker (str): company ticker symbol
        """
        ticker: str = str(kwargs["ticker"])

        loop = asyncio.get_running_loop()
        try:
            ticker_obj = yf.Ticker(ticker)
            info: dict[str, Any] = await loop.run_in_executor(None, lambda: ticker_obj.info)
        except Exception as exc:
            raise QivcDataError(f"EstimatesAgent yfinance.info failed for {ticker}: {exc}") from exc

        # yfinance info dict may contain currentQuarterEstimate and similar fields,
        # but does not expose 30/60/90-day revision deltas directly.
        # Attempt to read if present (future yfinance versions may add this).
        delta_30d: float | None = _to_float(info.get("epsRevision30d"))
        delta_60d: float | None = _to_float(info.get("epsRevision60d"))
        delta_90d: float | None = _to_float(info.get("epsRevision90d"))

        accelerating: bool = False
        if delta_30d is not None and delta_60d is not None and delta_90d is not None:
            accelerating = delta_30d > delta_60d > delta_90d

        return EpsRevisions(
            ticker=ticker,
            delta_30d=delta_30d,
            delta_60d=delta_60d,
            delta_90d=delta_90d,
            accelerating=accelerating,
        )


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        result = float(val)
        return None if result != result else result  # NaN guard
    except (TypeError, ValueError):
        return None
