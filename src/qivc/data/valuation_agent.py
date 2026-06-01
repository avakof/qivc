"""ValuationAgent — market valuation metrics via yfinance."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import yfinance as yf

from qivc.data import DataAgent
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError
from qivc.schemas import Valuation

log = logging.getLogger(__name__)

# Type alias for the sector-median lookup callable
SectorMedianProvider = Callable[[str, str], float | None]


def _default_sector_median_provider(sector: str, metric: str) -> float | None:
    """Fallback: no sector-median data available (returns None → UNVERIFIABLE)."""
    return None


class ValuationAgent(DataAgent[Valuation]):
    """
    Fetches valuation metrics from yfinance and looks up the sector median
    from an injected provider (wired to DuckDB in Phase 3+).
    """

    def __init__(
        self,
        client: EdgarClient,
        sector_median_provider: SectorMedianProvider | None = None,
    ) -> None:
        self._client = client
        self._median: SectorMedianProvider = (
            sector_median_provider or _default_sector_median_provider
        )

    @property
    def source_name(self) -> str:
        return "yfinance_valuation"

    async def fetch(self, **kwargs: Any) -> Valuation:
        """
        Keyword args:
            ticker (str): company ticker symbol
        """
        ticker: str = str(kwargs["ticker"])

        import asyncio

        loop = asyncio.get_running_loop()
        try:
            info: dict[str, Any] = await loop.run_in_executor(None, lambda: yf.Ticker(ticker).info)
        except Exception as exc:
            raise QivcDataError(f"ValuationAgent yfinance.info failed for {ticker}: {exc}") from exc

        sector: str = str(info.get("sector") or "Unknown")
        gics_industry: str = str(info.get("industry") or "Unknown")

        forward_pe: float | None = _to_float(info.get("forwardPE"))
        ev_ebitda: float | None = _to_float(info.get("enterpriseToEbitda"))
        # yfinance does not expose TBV directly; price-to-book is the closest proxy
        p_tbv: float | None = _to_float(info.get("priceToBook"))
        # P/AFFO is REIT-specific; not available in yfinance
        p_affo: float | None = None

        # Determine the primary metric for this sector
        from qivc.filters.valuation import SECTOR_VALUATION_METRIC  # avoids circular at import time

        metric_name = SECTOR_VALUATION_METRIC.get(sector, "forward_pe")
        sector_median = self._median(sector, metric_name)

        return Valuation(
            ticker=ticker,
            sector=sector,
            gics_industry=gics_industry,
            forward_pe=forward_pe,
            ev_ebitda=ev_ebitda,
            p_tbv=p_tbv,
            p_affo=p_affo,
            sector_median_metric_value=sector_median,
        )


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        result = float(val)
        return None if result != result else result  # NaN guard
    except (TypeError, ValueError):
        return None
