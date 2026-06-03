"""Unit tests for the technical-oversold factor (Task 12 Phase 1)."""

from __future__ import annotations

import pandas as pd
import pytest

from qivc.backtest.providers import make_technical_provider
from qivc.backtest.technical import (
    pct_below_high,
    technical_oversold_score,
    wilder_rsi,
)


def test_rsi_computation_matches_known_values() -> None:
    # Hand-constructed: deltas alternate +4 / -2 over 14 deltas (15 closes), so
    # avg_gain = 28/14 = 2, avg_loss = 14/14 = 1, RS = 2, RSI = 100 - 100/3 = 66.667.
    closes = [100, 104, 102, 106, 104, 108, 106, 110, 108, 112, 110, 114, 112, 116, 114]
    assert wilder_rsi(closes, 14) == pytest.approx(66.6667, abs=1e-3)
    # Monotonic up -> no losses -> RSI 100; monotonic down -> no gains -> RSI 0.
    assert wilder_rsi([float(x) for x in range(1, 20)], 14) == 100.0
    assert wilder_rsi([float(x) for x in range(20, 1, -1)], 14) == 0.0
    # Too little history -> None.
    assert wilder_rsi([1.0, 2.0, 3.0], 14) is None


def test_technical_score_oversold_ranks_higher() -> None:
    # Oversold: a steep decline (low RSI) ending ~40% below its window high.
    oversold = [100.0 - i for i in range(0, 40)]  # 100 -> 61, falling
    # Extended: a steady rise (high RSI) ending AT its window high.
    extended = [60.0 + i for i in range(0, 40)]  # 60 -> 99, rising to the top

    s_oversold = technical_oversold_score(oversold)
    s_extended = technical_oversold_score(extended)
    assert s_oversold is not None and s_extended is not None
    assert s_oversold > s_extended
    # Oversold should sit well above neutral; extended near zero (RSI 100, at high).
    assert s_oversold > 0.5
    assert s_extended < 0.5

    # Sanity on the two sub-signals at the extremes.
    assert pct_below_high(extended) == pytest.approx(0.0)  # at its high
    assert pct_below_high(oversold) and pct_below_high(oversold) > 0.0  # below its high


def test_technical_score_neutral_on_missing_price_history() -> None:
    # Fewer than RSI period + 1 closes (e.g. a recent IPO) -> None, which the
    # composite scorer maps to the neutral 0.5 percentile.
    assert technical_oversold_score([10.0, 11.0, 12.0, 11.5, 12.5]) is None
    # The provider returns None for a ticker with no usable history before as_of.
    close = pd.DataFrame(
        {"NEW": [10.0, 11.0]},
        index=pd.to_datetime(["2025-06-02", "2025-06-03"]),
    )
    prov = make_technical_provider(close)
    import datetime as dt

    assert prov("NEW", dt.date(2025, 6, 2)) is None  # nothing strictly before as_of
    assert prov("MISSING", dt.date(2025, 6, 2)) is None  # ticker absent


def test_technical_score_no_lookahead() -> None:
    # Build 80 trading days; the provider must use ONLY closes strictly before
    # as_of. Corrupting the post-as_of closes must NOT change the score.
    import datetime as dt

    idx = pd.bdate_range("2025-01-01", periods=80)
    prices = [100.0 + (i % 7) - 3 for i in range(80)]  # some wiggle for a real RSI
    as_of = dt.date(2025, 4, 1)

    clean = pd.DataFrame({"AAA": prices}, index=idx)
    corrupted = clean.copy()
    corrupted.loc[corrupted.index >= pd.Timestamp(as_of), "AAA"] = 1e9  # future garbage

    s_clean = make_technical_provider(clean)("AAA", as_of)
    s_corrupt = make_technical_provider(corrupted)("AAA", as_of)
    assert s_clean is not None
    assert s_clean == s_corrupt  # post-as_of changes are invisible -> no look-ahead
