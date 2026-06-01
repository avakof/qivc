"""RegimeAgent — market regime classification from FRED + yfinance."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from typing import Any

import httpx
import yfinance as yf

from qivc.data import DataAgent
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError
from qivc.schemas import MarketRegime

log = logging.getLogger(__name__)

_FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_FRED_SERIES = {
    "VIXCLS": "vix",
    "BAA10Y": "credit_spread",
    "T10Y3M": "yield_curve",
}

# Regime thresholds (see brief §7.3)
_VIX_RISK_MID_LOW = 25.0
_VIX_RISK_OFF = 35.0
_CREDIT_WIDEN_BPS = 50.0  # 60-day widening to trigger risk-mid
_YIELD_INVERSION_THRESHOLD = 0.0  # negative = inverted


def _parse_fred_csv(text: str) -> list[tuple[str, float]]:
    """Parse a FRED CSV response into (date_str, value) pairs, dropping '.' values."""
    rows: list[tuple[str, float]] = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # skip header
    for row in reader:
        if len(row) < 2:
            continue
        try:
            rows.append((row[0], float(row[1])))
        except ValueError:
            continue  # '.' placeholders from FRED
    return rows


def _sma(values: list[float], window: int) -> float:
    """Simple moving average of the last *window* values."""
    tail = values[-window:] if len(values) >= window else values
    return sum(tail) / len(tail) if tail else 0.0


def _classify_regime(
    vix_60d_sma: float,
    credit_spread_bps: float,
    credit_spread_60d_ago_bps: float,
    yield_curve_bps: float,
    eps_revisions_falling: bool = False,
) -> str:
    """Apply the 3-state regime logic from brief §7.3."""
    credit_widening = credit_spread_bps - credit_spread_60d_ago_bps

    # Risk-off: VIX > 35 OR (inverted curve AND falling EPS revisions)
    if vix_60d_sma > _VIX_RISK_OFF:
        return "risk-off"
    if yield_curve_bps < _YIELD_INVERSION_THRESHOLD and eps_revisions_falling:
        return "risk-off"

    # Risk-mid: VIX 25-35 OR credit spread widened > 50 bps in 60 days
    if _VIX_RISK_MID_LOW <= vix_60d_sma <= _VIX_RISK_OFF:
        return "risk-mid"
    if credit_widening > _CREDIT_WIDEN_BPS:
        return "risk-mid"

    return "risk-on"


class RegimeAgent(DataAgent[MarketRegime]):
    """
    Classifies the current market regime using:
    - FRED: VIXCLS, BAA10Y, T10Y3M
    - yfinance: IVE and IVW 12-month returns (value vs growth)
    """

    def __init__(
        self,
        client: EdgarClient,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client
        self._http = http_client

    @property
    def source_name(self) -> str:
        return "fred_yfinance_regime"

    async def fetch(self, **kwargs: Any) -> MarketRegime:
        if self._http is not None:
            return await self._fetch_with_client(self._http)

        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._fetch_with_client(client)

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> MarketRegime:
        # Fetch all FRED series concurrently
        tasks = {key: asyncio.create_task(self._get_fred(client, key)) for key in _FRED_SERIES}
        results: dict[str, list[tuple[str, float]]] = {}
        for key, task in tasks.items():
            try:
                results[key] = await task
            except Exception as exc:
                raise QivcDataError(f"FRED fetch failed for {key}: {exc}") from exc

        vix_rows = results["VIXCLS"]
        credit_rows = results["BAA10Y"]
        yield_rows = results["T10Y3M"]

        vix_values = [v for _, v in vix_rows]
        credit_values = [v * 100 for _, v in credit_rows]  # % → bps
        yield_values = [v * 100 for _, v in yield_rows]  # % → bps

        if not vix_values:
            raise QivcDataError("FRED VIXCLS returned no data")

        vix_60d_sma = _sma(vix_values, 60)
        credit_now = credit_values[-1] if credit_values else 0.0
        if len(credit_values) > 60:
            credit_60d_ago = credit_values[-61]
        elif credit_values:
            credit_60d_ago = credit_values[0]
        else:
            credit_60d_ago = 0.0
        yield_now = yield_values[-1] if yield_values else 0.0

        # IVE vs IVW 12-month returns via yfinance
        loop = asyncio.get_running_loop()
        try:
            value_growth_12m: float = await loop.run_in_executor(None, _compute_value_growth_12m)
        except Exception as exc:
            log.warning("Could not compute value-growth spread: %s", exc)
            value_growth_12m = 0.0

        regime = _classify_regime(
            vix_60d_sma=vix_60d_sma,
            credit_spread_bps=credit_now,
            credit_spread_60d_ago_bps=credit_60d_ago,
            yield_curve_bps=yield_now,
        )

        return MarketRegime(
            vix_60d_sma=vix_60d_sma,
            credit_spread_bps=credit_now,
            yield_curve_bps=yield_now,
            value_growth_12m=value_growth_12m,
            regime=regime,
        )

    async def _get_fred(self, client: httpx.AsyncClient, series_id: str) -> list[tuple[str, float]]:
        url = f"{_FRED_BASE}?id={series_id}"
        response = await client.get(url)
        response.raise_for_status()
        return _parse_fred_csv(response.text)


def _compute_value_growth_12m() -> float:
    """Return IVE 12m total return minus IVW 12m total return."""

    def _annual_return(ticker: str) -> float:
        hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if hist.empty:
            return 0.0
        first, last = hist["Close"].iloc[0], hist["Close"].iloc[-1]
        return (last - first) / first if first else 0.0

    ive = _annual_return("IVE")
    ivw = _annual_return("IVW")
    return ive - ivw
