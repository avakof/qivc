"""
Reference-only technical indicators for the ticker drill-down (Task 18).

DISPLAY ONLY. None of these feed the composite score, the candidacy, any weight, or
the scorer — the technical factor stays SHELVED at 0% (failed the 2022 PIT regime
test). They are context for a human reading one name. Pure functions over OHLCV
lists (oldest first); each value comes with a plain-language reading. Insufficient
history -> the row is omitted (value None).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

from qivc.backtest.technical import pct_below_high, wilder_rsi


def _sma(xs: Sequence[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _ema_series(xs: Sequence[float], n: int) -> list[float]:
    if len(xs) < n:
        return []
    k = 2.0 / (n + 1)
    ema = sum(xs[:n]) / n
    out = [ema]
    for x in xs[n:]:
        ema = x * k + ema * (1 - k)
        out.append(ema)
    return out


def _wilder_smooth(xs: Sequence[float], n: int) -> list[float]:
    """Wilder's smoothing (for ADX): seed = sum of first n, then s = s - s/n + x."""
    if len(xs) < n:
        return []
    s = sum(xs[:n])
    out = [s]
    for x in xs[n:]:
        s = s - s / n + x
        out.append(s)
    return out


def _row(name: str, value: float | None, fmt: str, reading: str) -> dict[str, Any]:
    return {"name": name, "value": None if value is None else fmt.format(value), "reading": reading}


# ---------------------------------------------------------------------------
# Individual indicators (return latest value or None)
# ---------------------------------------------------------------------------
def macd(closes: Sequence[float]) -> tuple[float, float, float] | None:
    e12, e26 = _ema_series(closes, 12), _ema_series(closes, 26)
    if not e12 or not e26:
        return None
    n = min(len(e12), len(e26))
    line = [e12[-n + i] - e26[-n + i] for i in range(n)]  # aligned tail
    sig = _ema_series(line, 9)
    if not sig:
        return None
    return line[-1], sig[-1], line[-1] - sig[-1]


def roc(closes: Sequence[float], n: int = 20) -> float | None:
    if len(closes) < n + 1 or closes[-n - 1] == 0:
        return None
    return (closes[-1] / closes[-n - 1] - 1) * 100


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14
        ) -> float | None:
    if len(closes) < 2 * n + 1:
        return None
    tr, pdm, ndm = [], [], []
    for i in range(1, len(closes)):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    atr = _wilder_smooth(tr, n)
    sp, sn = _wilder_smooth(pdm, n), _wilder_smooth(ndm, n)
    m = min(len(atr), len(sp), len(sn))
    dx = []
    for i in range(m):
        a = atr[-m + i]
        if a == 0:
            dx.append(0.0)
            continue
        pdi, ndi = 100 * sp[-m + i] / a, 100 * sn[-m + i] / a
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0.0)
    if len(dx) < n:
        return None
    return sum(dx[-n:]) / n


def stochastic(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               n: int = 14) -> tuple[float, float] | None:
    if len(closes) < n + 2:
        return None
    ks = []
    for i in range(n - 1, len(closes)):
        hh, ll = max(highs[i - n + 1:i + 1]), min(lows[i - n + 1:i + 1])
        ks.append(100 * (closes[i] - ll) / (hh - ll) if hh > ll else 50.0)
    d = _sma(ks, 3)
    return ks[-1], (d if d is not None else ks[-1])


def williams_r(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               n: int = 14) -> float | None:
    if len(closes) < n:
        return None
    hh, ll = max(highs[-n:]), min(lows[-n:])
    return -100 * (hh - closes[-1]) / (hh - ll) if hh > ll else -50.0


def bollinger(closes: Sequence[float], n: int = 20, k: float = 2.0
              ) -> tuple[float, float] | None:
    if len(closes) < n:
        return None
    mid = _sma(closes, n)
    sd = statistics.pstdev(closes[-n:])
    if mid is None or sd == 0:
        return None
    upper, lower = mid + k * sd, mid - k * sd
    pct_b = (closes[-1] - lower) / (upper - lower)
    bandwidth = (upper - lower) / mid * 100
    return pct_b, bandwidth


def cci(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 20
        ) -> float | None:
    if len(closes) < n:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    sma_tp = sum(tp[-n:]) / n
    mad = sum(abs(x - sma_tp) for x in tp[-n:]) / n
    return (tp[-1] - sma_tp) / (0.015 * mad) if mad else 0.0


def money_flow_index(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                     volumes: Sequence[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    pos = neg = 0.0
    for i in range(len(tp) - n, len(tp)):
        rmf = tp[i] * volumes[i]
        if tp[i] > tp[i - 1]:
            pos += rmf
        elif tp[i] < tp[i - 1]:
            neg += rmf
    if neg == 0:
        return 100.0
    return 100 - 100 / (1 + pos / neg)


def obv_trend(closes: Sequence[float], volumes: Sequence[float], n: int = 20
              ) -> str | None:
    if len(closes) < n + 1:
        return None
    obv = [0.0]
    for i in range(1, len(closes)):
        obv.append(obv[-1] + (volumes[i] if closes[i] > closes[i - 1]
                              else -volumes[i] if closes[i] < closes[i - 1] else 0.0))
    return "rising" if obv[-1] > obv[-n - 1] else "falling" if obv[-1] < obv[-n - 1] else "flat"


def volume_vs_avg(volumes: Sequence[float], n: int = 20) -> float | None:
    if len(volumes) < n + 1:
        return None
    avg = sum(volumes[-n - 1:-1]) / n
    return volumes[-1] / avg if avg else None


# ---------------------------------------------------------------------------
# Grouped panel with readings
# ---------------------------------------------------------------------------
def compute_indicators(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    volumes: Sequence[float],
) -> dict[str, list[dict[str, Any]]]:
    """Three labelled groups of reference indicators (display-only; rows omitted when None)."""
    mom: list[dict[str, Any]] = []
    rev: list[dict[str, Any]] = []
    vol: list[dict[str, Any]] = []

    rsi = wilder_rsi(closes)
    if rsi is not None:
        rd = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral"
        mom.append(_row("RSI(14)", rsi, "{:.1f}", rd))
    mc = macd(closes)
    if mc is not None:
        ln, sg, hi = mc
        mom.append(_row("MACD(12/26/9) hist", hi, "{:+.2f}",
                        f"line {ln:+.2f} / signal {sg:+.2f} — "
                        + ("bullish (line > signal)" if hi > 0 else "bearish (line < signal)")))
    rc = roc(closes, 20)
    if rc is not None:
        mom.append(_row("Rate of Change(20d)", rc, "{:+.1f}%",
                        "up over 20d" if rc > 0 else "down over 20d"))
    s50, s200 = _sma(closes, 50), _sma(closes, 200)
    if s50:
        v = (closes[-1] / s50 - 1) * 100
        mom.append(_row("Price vs SMA(50)", v, "{:+.1f}%",
                        "above 50-day avg" if v >= 0 else "below 50-day avg"))
    if s200:
        v = (closes[-1] / s200 - 1) * 100
        mom.append(_row("Price vs SMA(200)", v, "{:+.1f}%",
                        "above 200-day avg" if v >= 0 else "below 200-day avg"))
    ax = adx(highs, lows, closes)
    if ax is not None:
        rd = "trending" if ax > 25 else "choppy/range-bound" if ax < 20 else "transitional"
        mom.append(_row("ADX(14)", ax, "{:.1f}", rd + " (trend-strength context)"))

    st = stochastic(highs, lows, closes)
    if st is not None:
        kk, dd = st
        rd = "overbought" if kk > 80 else "oversold" if kk < 20 else "neutral"
        rev.append(_row("Stochastic %K/%D(14)", kk, "{:.0f}", f"%D {dd:.0f} — {rd}"))
    wr = williams_r(highs, lows, closes)
    if wr is not None:
        rd = "overbought" if wr > -20 else "oversold" if wr < -80 else "neutral"
        rev.append(_row("Williams %R(14)", wr, "{:.0f}", rd))
    bb = bollinger(closes)
    if bb is not None:
        pb, bw = bb
        rd = ("above upper band" if pb > 1 else "below lower band" if pb < 0
              else "within bands")
        rev.append(_row("Bollinger %B(20,2sd)", pb, "{:.2f}", f"bandwidth {bw:.1f}% - {rd}"))
    pbh = pct_below_high(closes)
    if pbh is not None:
        rev.append(_row("% below 60-day high", pbh * 100, "{:.1f}%",
                        "at/near high" if pbh < 0.05 else "well off high" if pbh > 0.25
                        else "modestly off high"))
    cc = cci(highs, lows, closes)
    if cc is not None:
        rd = "overbought" if cc > 100 else "oversold" if cc < -100 else "neutral"
        rev.append(_row("CCI(20)", cc, "{:.0f}", rd))

    mfi = money_flow_index(highs, lows, closes, volumes)
    if mfi is not None:
        rd = "oversold" if mfi < 20 else "overbought" if mfi > 80 else "neutral"
        vol.append(_row("Money Flow Index(14)", mfi, "{:.1f}",
                        rd + " (volume-weighted RSI · Baldwin Filter 4)"))
    ot = obv_trend(closes, volumes)
    if ot is not None:
        vol.append({"name": "On-Balance Volume (vs 20d)", "value": ot,
                    "reading": "accumulation" if ot == "rising"
                    else "distribution" if ot == "falling" else "flat"})
    vv = volume_vs_avg(volumes)
    if vv is not None:
        rd = "elevated" if vv > 1.5 else "light" if vv < 0.5 else "normal"
        vol.append(_row("Volume vs 20-day avg", vv, "{:.2f}x", rd))

    return {"momentum": mom, "reversion": rev, "volume": vol}
