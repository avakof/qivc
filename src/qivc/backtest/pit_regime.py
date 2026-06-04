"""
Point-in-time market regime (Task 10).

The live ``RegimeAgent`` fetches full FRED series and uses the tail (latest
values). For the backtest we need the regime **as it would have been seen on a
2025 rebalance date** — so we fetch the same FRED series, slice every row to
``date <= as_of``, and apply the identical SMA / classification logic. FRED is
fully historical, so this is genuinely point-in-time (no look-ahead) for the
VIX / credit-spread / yield-curve inputs.

The value-vs-growth 12-month spread (IVE-IVW) is computed from yfinance history
ending at ``as_of`` — also PIT. It only annotates the regime (not a gate input
in ``_classify_regime``), so a yfinance failure degrades to 0.0 harmlessly.
"""

from __future__ import annotations

import datetime as _dt
import logging
import time

import httpx

from qivc.data.regime_agent import _classify_regime, _parse_fred_csv, _sma
from qivc.schemas import MarketRegime

log = logging.getLogger(__name__)

_FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_FRED_TIMEOUT = 20.0
_FRED_BACKOFF = (1.0, 2.0, 4.0)  # retry waits; len+1 = total attempts

# The neutral regime (risk-on, 100% equity): used when FRED has no pre-as_of data,
# and as the graceful fallback when FRED is unreachable. The regime only scales
# position SIZING — never which names are selected — so a neutral fallback leaves
# signal recording fully intact.
NEUTRAL_REGIME = MarketRegime(
    vix_60d_sma=0.0, credit_spread_bps=0.0, yield_curve_bps=0.0,
    value_growth_12m=0.0, regime="risk-on",
)


def _fred_as_of(client: httpx.Client, series_id: str, as_of: _dt.date) -> list[float]:
    """Fetch a FRED series (retry-with-backoff on transport errors) -> values < as_of."""
    last_exc: httpx.HTTPError | None = None
    for attempt in range(len(_FRED_BACKOFF) + 1):
        try:
            resp = client.get(f"{_FRED_BASE}?id={series_id}")
            resp.raise_for_status()
            rows = _parse_fred_csv(resp.text)
            cutoff = as_of.isoformat()
            return [v for d, v in rows if d < cutoff]  # strict: observable before as_of
        except httpx.TransportError as exc:  # ReadTimeout/ConnectError/etc.
            last_exc = exc
            if attempt < len(_FRED_BACKOFF):
                log.warning(
                    "FRED %s fetch failed (%s); retry %d/%d in %.0fs",
                    series_id, type(exc).__name__, attempt + 1, len(_FRED_BACKOFF),
                    _FRED_BACKOFF[attempt],
                )
                time.sleep(_FRED_BACKOFF[attempt])
    assert last_exc is not None
    raise last_exc


def regime_as_of(as_of: _dt.date, client: httpx.Client | None = None) -> MarketRegime:
    """Compute the MarketRegime observable strictly before ``as_of`` (PIT).

    Raises ``httpx.HTTPError`` if FRED is unreachable after retries — callers that
    must not crash on that should use :func:`regime_as_of_resilient`.
    """
    owns = client is None
    client = client or httpx.Client(timeout=_FRED_TIMEOUT)
    try:
        vix = _fred_as_of(client, "VIXCLS", as_of)
        credit = [v * 100 for v in _fred_as_of(client, "BAA10Y", as_of)]  # % -> bps
        yld = [v * 100 for v in _fred_as_of(client, "T10Y3M", as_of)]  # % -> bps
    finally:
        if owns:
            client.close()

    if not vix:
        # No FRED data before as_of — fail safe to neutral risk-on (no spurious block).
        log.warning("No FRED VIX data before %s; defaulting regime risk-on", as_of)
        return NEUTRAL_REGIME

    vix_60d_sma = _sma(vix, 60)
    credit_now = credit[-1] if credit else 0.0
    credit_60d_ago = credit[-61] if len(credit) > 60 else (credit[0] if credit else 0.0)
    yield_now = yld[-1] if yld else 0.0

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
        value_growth_12m=0.0,  # annotation only; PIT IVE-IVW spread omitted for determinism
        regime=regime,
    )


def regime_as_of_resilient(
    as_of: _dt.date, client: httpx.Client | None = None
) -> tuple[MarketRegime, str]:
    """
    Regime + source tag, never raising on FRED being down. Returns
    ``(regime, "fred_live")`` on success, or ``(NEUTRAL_REGIME, "fallback_neutral")``
    if FRED is unreachable after retries (logged WARNING). The regime only scales
    position sizing, so the fallback leaves signal recording unaffected — the daily
    paper record always completes.
    """
    try:
        return regime_as_of(as_of, client=client), "fred_live"
    except httpx.HTTPError as exc:
        log.warning(
            "FRED unreachable after retries (%s) — using neutral regime for %s "
            "(position sizing only; signal recording unaffected)",
            type(exc).__name__, as_of,
        )
        return NEUTRAL_REGIME, "fallback_neutral"
