"""Tests for the reference-only technical indicators (Task 18) — display only."""

from __future__ import annotations

import pytest

from qivc.dashboard.indicators import (
    compute_indicators,
    macd,
    money_flow_index,
    roc,
    stochastic,
)


def _rows(panel: dict, group: str) -> dict[str, dict]:
    return {r["name"]: r for r in panel[group]}


def test_compute_indicators_known_values() -> None:
    # gently declining series, 260 bars -> enough for SMA200 / ADX
    closes = [300.0 - i * 0.5 for i in range(260)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    vols = [1000.0] * 260

    # hand-checks
    assert roc(closes, 20) == pytest.approx((closes[-1] / closes[-21] - 1) * 100)
    kk, _dd = stochastic(highs, lows, closes)  # type: ignore[misc]
    assert kk < 20                                # close near the bottom of the range (oversold)
    assert money_flow_index(highs, lows, closes, vols) == pytest.approx(0.0)  # all down days
    assert macd(closes) is not None

    panel = compute_indicators(highs, lows, closes, vols)
    mom = _rows(panel, "momentum")
    assert mom["RSI(14)"]["value"] == "0.0" and mom["RSI(14)"]["reading"] == "oversold"
    assert "down over 20d" in mom["Rate of Change(20d)"]["reading"]
    vol = _rows(panel, "volume")
    assert vol["Volume vs 20-day avg"]["value"] == "1.00x"            # constant volume
    assert vol["Money Flow Index(14)"]["reading"].startswith("oversold")
    # three groups, all populated
    assert panel["momentum"] and panel["reversion"] and panel["volume"]


def test_compute_indicators_insufficient_history() -> None:
    closes = [10.0, 10.1, 9.9, 10.2, 10.0]      # 5 bars — far too few
    panel = compute_indicators([c + 1 for c in closes], [c - 1 for c in closes],
                               closes, [100.0] * 5)
    # nothing requiring long windows computes; groups are empty (graceful)
    assert panel["momentum"] == [] or all(
        r["name"] != "Price vs SMA(200)" for r in panel["momentum"]
    )
    assert panel["volume"] == [] or all(r["value"] is not None for r in panel["volume"])
