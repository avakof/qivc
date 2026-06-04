"""
yfinance price provider for the dashboard, with a short-TTL raw-price cache (Task 15).

Caches the raw daily-close matrix to disk; if the cache is younger than the TTL
(~15 min) and covers the requested span, it is reused so repeated dashboard runs
don't hammer yfinance. Caches RAW prices only — never derived metrics.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CACHE = "data/paper/_price_cache.json"
_OHLCV_CACHE = "data/paper/_ohlcv_cache.json"
_TTL_SECONDS = 15 * 60


class YFinancePriceProvider:
    """Live daily closes via yfinance, cached with a short TTL (raw prices only)."""

    def __init__(self, cache_path: str = DEFAULT_CACHE, now: _dt.datetime | None = None) -> None:
        self._cache_path = Path(cache_path)
        self._now = now  # injectable for tests

    def daily_closes(
        self, tickers: list[str], start: _dt.date, end: _dt.date
    ) -> dict[str, dict[str, float]]:
        key = f"{min(tickers)}|{max(tickers)}|{len(tickers)}|{start}|{end}"
        cached = self._read_cache(key)
        if cached is not None:
            return cached
        data = self._fetch(tickers, start, end)
        self._write_cache(key, data)
        return data

    # -- cache (TTL on file mtime) --
    def _read_cache(self, key: str) -> dict[str, dict[str, float]] | None:
        p = self._cache_path
        if not p.exists():
            return None
        age = (self._mtime_now() - p.stat().st_mtime)
        if age > _TTL_SECONDS:
            return None
        blob = json.loads(p.read_text())
        if blob.get("key") != key:
            return None
        return blob["data"]  # type: ignore[no-any-return]

    def _write_cache(self, key: str, data: dict[str, dict[str, float]]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps({"key": key, "data": data}))

    def _mtime_now(self) -> float:
        if self._now is not None:
            return self._now.timestamp()
        import time

        return time.time()

    def daily_ohlcv(
        self, ticker: str, start: _dt.date, end: _dt.date
    ) -> dict[str, dict[str, float]]:
        """{high|low|close|volume: {ISO date: value}} for one ticker (cached, TTL)."""
        key = f"{ticker}|{start}|{end}"
        p = Path(_OHLCV_CACHE)
        if p.exists() and (self._mtime_now() - p.stat().st_mtime) <= _TTL_SECONDS:
            blob = json.loads(p.read_text())
            if blob.get("key") == key:
                return blob["data"]  # type: ignore[no-any-return]
        data = self._fetch_ohlcv(ticker, start, end)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"key": key, "data": data}))
        return data

    def _fetch_ohlcv(
        self, ticker: str, start: _dt.date, end: _dt.date
    ) -> dict[str, dict[str, float]]:
        import yfinance as yf

        raw = yf.download(
            ticker, start=str(start), end=str(end + _dt.timedelta(days=2)),
            progress=False, auto_adjust=True,
        )
        out: dict[str, dict[str, float]] = {"high": {}, "low": {}, "close": {}, "volume": {}}
        if raw is None or not hasattr(raw, "columns") or raw.empty:
            return out
        for field, key in (("High", "high"), ("Low", "low"), ("Close", "close"),
                           ("Volume", "volume")):
            col = raw[field]
            if hasattr(col, "columns"):  # single-ticker DataFrame under MultiIndex
                col = col.iloc[:, 0]
            out[key] = {d.date().isoformat(): float(v) for d, v in col.dropna().items()}
        return out

    # -- fetch --
    def _fetch(
        self, tickers: list[str], start: _dt.date, end: _dt.date
    ) -> dict[str, dict[str, float]]:
        import yfinance as yf

        raw = yf.download(
            tickers, start=str(start), end=str(end + _dt.timedelta(days=2)),
            progress=False, auto_adjust=True, group_by="column",
        )
        close = raw.get("Close", raw)
        out: dict[str, dict[str, float]] = {}
        if hasattr(close, "columns"):
            for t in close.columns:
                ser = close[t].dropna()
                out[str(t)] = {d.date().isoformat(): float(v) for d, v in ser.items()}
        else:  # single ticker -> Series
            ser = close.dropna()
            out[tickers[0]] = {d.date().isoformat(): float(v) for d, v in ser.items()}
        return out
