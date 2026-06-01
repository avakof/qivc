"""LiquidityAgent — market cap, ADV, earnings date, M&A flag."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any

import yfinance as yf

from qivc.data import DataAgent
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError
from qivc.schemas import Liquidity

log = logging.getLogger(__name__)

_MA_PATTERN = re.compile(r"Item\s+1\.01\s+Material\s+Definitive\s+Agreement", re.IGNORECASE)


class LiquidityAgent(DataAgent[Liquidity]):
    """Fetches liquidity metrics and flags pending M&A from recent 8-K filings."""

    def __init__(self, client: EdgarClient) -> None:
        self._client = client

    @property
    def source_name(self) -> str:
        return "yfinance_liquidity"

    async def fetch(self, **kwargs: Any) -> Liquidity:
        """
        Keyword args:
            ticker (str): company ticker symbol
            position_size_usd (float): intended position size in USD for ADV check
        """
        ticker: str = str(kwargs["ticker"])

        loop = asyncio.get_running_loop()
        try:
            ticker_obj = yf.Ticker(ticker)
            info: dict[str, Any] = await loop.run_in_executor(None, lambda: ticker_obj.info)
        except Exception as exc:
            raise QivcDataError(f"LiquidityAgent yfinance.info failed for {ticker}: {exc}") from exc

        market_cap: float = _safe_float(info.get("marketCap"))

        # ADV in dollars — prefer 20-day average
        avg_volume = _safe_float(info.get("averageVolume"))  # 30d average by default
        avg_price = _safe_float(info.get("regularMarketPrice") or info.get("currentPrice"))
        if avg_price <= 0:
            avg_price = _safe_float(info.get("previousClose"))

        adv_20d_usd: float = avg_volume * avg_price

        # Earnings date
        next_earnings: date | None = None
        try:
            cal: Any = await loop.run_in_executor(None, lambda: ticker_obj.calendar)
            if cal is not None:
                # calendar may be a dict or DataFrame depending on yfinance version
                if isinstance(cal, dict):
                    raw_earnings = cal.get("Earnings Date") or cal.get("earningsDate")
                    if raw_earnings:
                        if isinstance(raw_earnings, list):
                            raw_earnings = raw_earnings[0]
                        next_earnings = _parse_date(raw_earnings)
                else:
                    # DataFrame case — take first row
                    try:
                        raw_earnings = cal.iloc[0, 0]
                        next_earnings = _parse_date(raw_earnings)
                    except Exception:
                        pass
        except Exception as exc:
            log.debug("Could not fetch earnings calendar for %s: %s", ticker, exc)

        # M&A flag: search recent 8-K filings for Item 1.01 language
        has_pending_ma = False
        try:
            texts = await self._client.get_recent_8k_texts(ticker, lookback_days=60)
            has_pending_ma = any(_MA_PATTERN.search(t) for t in texts if t)
        except Exception as exc:
            log.debug("Could not check 8-K filings for M&A on %s: %s", ticker, exc)

        return Liquidity(
            ticker=ticker,
            market_cap_usd=market_cap,
            adv_20d_usd=adv_20d_usd,
            next_earnings_date=next_earnings,
            has_pending_ma=has_pending_ma,
        )


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_date(val: Any) -> date | None:
    if val is None:
        return None
    try:
        if hasattr(val, "date") and callable(val.date):
            result: date = val.date()
            return result
        if isinstance(val, date):
            return val
        return date.fromisoformat(str(val)[:10])
    except (ValueError, AttributeError):
        return None
