"""
Technical-oversold factor (Task 12 Phase 1), inspired by the Baldwin Filter-4
methodology. PRE-REGISTERED parameters — do NOT tune these on data:

  - RSI period            = 14 (standard Wilder)
  - recent-high lookback  = 60 trading days
  - sub-signal blend      = 0.5 / 0.5

The per-stock raw technical-oversold score blends two [0, 1] sub-signals:

  1. Inverted RSI(14):  (100 - RSI) / 100   (more oversold -> higher)
  2. Distance below the 60-day high: (high60 - close) / high60, clamped [0, 1]
     (a beaten-down name scores high; one at its high scores 0)

  technical_raw = 0.5 * inverted_rsi + 0.5 * pct_below_high

The raw value is later percentile-ranked within the universe by the composite
scorer (same convention as the other raw factors), so 0.5 weights here are on the
*sub-signals*, not the cross-sectional rank.

All inputs are strictly point-in-time: callers pass only closes dated BEFORE the
as-of date. Insufficient history (e.g. a recent IPO) -> None -> the scorer treats
it as the neutral 0.5 percentile, same as the other factors' missing-data rule.
"""

from __future__ import annotations

from collections.abc import Sequence

RSI_PERIOD = 14
HIGH_LOOKBACK = 60
_SUBSIGNAL_BLEND = 0.5


def wilder_rsi(closes: Sequence[float], period: int = RSI_PERIOD) -> float | None:
    """
    Wilder's RSI over *period* from a chronological close series (oldest first,
    most recent last). Returns None if there are fewer than period+1 closes.
    Returns 100.0 when there are no losses in the window.
    """
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    # Seed with the simple average over the first `period` deltas...
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # ...then apply Wilder smoothing across the remaining deltas.
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def pct_below_high(closes: Sequence[float], lookback: int = HIGH_LOOKBACK) -> float | None:
    """
    Fraction below the highest close in the last *lookback* closes, clamped
    [0, 1]. The current close is closes[-1]; the high is the max over the window
    (which includes the current close, so a name at its high scores 0). Returns
    None on an empty series.
    """
    if not closes:
        return None
    window = closes[-lookback:]
    high = max(window)
    if high <= 0:
        return None
    current = closes[-1]
    frac = (high - current) / high
    return max(0.0, min(1.0, frac))


def technical_oversold_score(
    closes: Sequence[float],
    rsi_period: int = RSI_PERIOD,
    high_lookback: int = HIGH_LOOKBACK,
) -> float | None:
    """
    Pre-registered technical-oversold raw score in [0, 1] from a chronological,
    point-in-time close series (oldest first). Returns None if RSI cannot be
    computed (insufficient history) -> caller maps to neutral 0.5.
    """
    rsi = wilder_rsi(closes, rsi_period)
    pbh = pct_below_high(closes, high_lookback)
    if rsi is None or pbh is None:
        return None
    inverted_rsi = (100.0 - rsi) / 100.0
    return _SUBSIGNAL_BLEND * inverted_rsi + (1.0 - _SUBSIGNAL_BLEND) * pbh
