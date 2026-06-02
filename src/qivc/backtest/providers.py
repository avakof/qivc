"""
Cached point-in-time data providers for the 2025 backtest (Task 10).

All external data (EDGAR fundamentals, yfinance prices/info, FRED/yfinance rates)
is fetched once and cached to disk as JSON/CSV. The backtest then reads only from
the cache, which is the **reproducibility anchor**: a re-run off the same cache is
byte-identical (live endpoints revise data over time, so the cache — not the live
call — is what makes "same seed -> same outputs" hold).

Point-in-time discipline:
  - Fundamentals: most recent 10-K/10-Q FILED strictly before as_of (EDGAR).
  - Prices/volume: daily history; the engine only ever indexes dates <= as_of.
  - Liquidity market cap uses PIT price x shares-outstanding; shares come from
    yfinance.info (current) — a slowly-varying quantity, a minor documented
    approximation (BACKTEST_LIMITATIONS.md). ADV is fully PIT (price x volume).
  - Industry (for the sub-sector cap) is current yfinance.info — classification,
    not a P&L input.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from qivc.data.edgar_client import EdgarClient
from qivc.schemas import Fundamentals, Liquidity

log = logging.getLogger(__name__)

_MIN_MARKET_CAP = 300_000_000.0


class BacktestCache:
    """Disk cache for all PIT inputs (one directory per backtest data set)."""

    def __init__(self, cache_dir: Path) -> None:
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _json_path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def get_json(self, name: str) -> Any | None:
        p = self._json_path(name)
        if p.exists():
            return json.loads(p.read_text())
        return None

    def put_json(self, name: str, payload: Any) -> None:
        self._json_path(name).write_text(json.dumps(payload, sort_keys=True, default=str))


# --------------------------------------------------------------------------
# Prices (yfinance, cached to one CSV)
# --------------------------------------------------------------------------


def build_price_cache(
    cache: BacktestCache,
    tickers: list[str],
    start: _dt.date,
    end: _dt.date,
) -> pd.DataFrame:
    """Download daily close + volume for *tickers*; cache to prices.csv / volume.csv."""
    close_p = cache.dir / "prices.csv"
    vol_p = cache.dir / "volume.csv"
    if close_p.exists() and vol_p.exists():
        close = pd.read_csv(close_p, index_col=0, parse_dates=True)
        return close
    raw = yf.download(
        tickers, start=str(start), end=str(end), progress=False, auto_adjust=True, group_by="column"
    )
    close = raw.get("Close", raw)
    vol = raw.get("Volume", raw)
    if isinstance(close, pd.Series):  # single ticker
        close = close.to_frame(tickers[0])
        vol = vol.to_frame(tickers[0])
    close.to_csv(close_p)
    vol.to_csv(vol_p)
    return close


def load_prices(cache: BacktestCache) -> tuple[pd.DataFrame, pd.DataFrame]:
    close = pd.read_csv(cache.dir / "prices.csv", index_col=0, parse_dates=True)
    vol = pd.read_csv(cache.dir / "volume.csv", index_col=0, parse_dates=True)
    return close, vol


# --------------------------------------------------------------------------
# Liquidity provider (PIT, from cached prices + cached shares/industry)
# --------------------------------------------------------------------------


def fetch_info_cache(cache: BacktestCache, tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Cache yfinance .info shares-outstanding + sector/industry per ticker."""
    cached = cache.get_json("info")
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    info: dict[str, dict[str, Any]] = {}
    for t in tickers:
        try:
            raw = yf.Ticker(t).info
            info[t] = {
                "shares_outstanding": raw.get("sharesOutstanding"),
                "industry": raw.get("industry") or "Unknown",
                "sector": raw.get("sector") or "Unknown",
            }
        except Exception as exc:
            log.warning("info fetch failed for %s: %s", t, exc)
            info[t] = {"shares_outstanding": None, "industry": "Unknown", "sector": "Unknown"}
    cache.put_json("info", info)
    return info


def make_liquidity_provider(
    close: pd.DataFrame,
    vol: pd.DataFrame,
    info: dict[str, dict[str, Any]],
) -> Callable[[str, _dt.date], Liquidity | None]:
    """Build a PIT liquidity provider: market cap (price x shares) + 20d ADV."""

    def provider(ticker: str, as_of: _dt.date) -> Liquidity | None:
        if ticker not in close.columns:
            return None
        ts = pd.Timestamp(as_of)
        px = close[ticker].loc[close.index < ts].dropna()  # strictly before as_of
        vols = vol[ticker].loc[vol.index < ts].dropna()
        if px.empty:
            return None
        last_px = float(px.iloc[-1])
        shares = (info.get(ticker) or {}).get("shares_outstanding")
        market_cap = last_px * float(shares) if shares else 0.0
        adv20 = float((px.tail(20) * vols.tail(20)).mean()) if not vols.empty else 0.0
        return Liquidity(
            ticker=ticker,
            market_cap_usd=market_cap,
            adv_20d_usd=adv20,
            next_earnings_date=None,  # not modeled PIT -> no earnings blackout in backtest
            has_pending_ma=False,
        )

    return provider


def make_industry_provider(info: dict[str, dict[str, Any]]) -> Callable[[str], str]:
    def provider(ticker: str) -> str:
        return str((info.get(ticker) or {}).get("industry") or "Unknown")

    return provider


# --------------------------------------------------------------------------
# Fundamentals provider (EDGAR PIT, cached)
# --------------------------------------------------------------------------


def build_fundamentals_cache(
    cache: BacktestCache,
    client: EdgarClient,
    ticker_dates: list[tuple[str, _dt.date]],
) -> None:
    """Fetch + cache PIT fundamentals for each (ticker, as_of) the backtest needs."""
    import asyncio

    from qivc.backtest.pit import get_fundamentals_as_of
    from qivc.data.fundamentals_agent import _compute_fundamentals

    existing = cache.get_json("fundamentals") or {}

    async def _fetch_one(ticker: str, as_of: _dt.date) -> dict[str, Any] | None:
        try:
            fin = await get_fundamentals_as_of(client, ticker, as_of)
            if fin is None:
                return None
            fund = _compute_fundamentals(ticker, "PIT", fin, None)
            return fund.model_dump(mode="json")
        except Exception as exc:
            log.warning("fundamentals fetch failed for %s @ %s: %s", ticker, as_of, exc)
            return None

    async def _run() -> None:
        for ticker, as_of in ticker_dates:
            key = f"{ticker}|{as_of.isoformat()}"
            if key in existing:
                continue
            existing[key] = await _fetch_one(ticker, as_of)

    asyncio.run(_run())
    cache.put_json("fundamentals", existing)


def make_fundamentals_provider(
    cache: BacktestCache,
) -> Callable[[str, _dt.date], Fundamentals | None]:
    data = cache.get_json("fundamentals") or {}

    def provider(ticker: str, as_of: _dt.date) -> Fundamentals | None:
        rec = data.get(f"{ticker}|{as_of.isoformat()}")
        if rec is None:
            return None
        return Fundamentals(**rec)

    return provider
