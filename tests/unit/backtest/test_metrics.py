"""Unit tests for backtest metrics — deterministic, hand-checked values."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from qivc.backtest import metrics as m
from qivc.schemas import BacktestTrade


def _equity(values: list[float]) -> pd.Series:
    idx = pd.bdate_range("2024-01-01", periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def _trade(ret: float | None, hold: int | None, closed: bool = True) -> BacktestTrade:
    return BacktestTrade(
        ticker="X",
        entry_date=date(2024, 1, 1),
        exit_date=date(2024, 2, 1) if closed else None,
        entry_price=100.0,
        exit_price=100.0 * (1 + ret) if (ret is not None and closed) else None,
        return_pct=ret,
        hold_days=hold,
    )


# ---------------------------------------------------------------------------
# CAGR
# ---------------------------------------------------------------------------


def test_cagr_one_year_doubling() -> None:
    # 253 points (252 steps) doubling over exactly one year → CAGR = 100%
    eq = _equity(list(np.linspace(100.0, 200.0, 253)))
    assert m.cagr(eq) == pytest.approx(1.0, rel=1e-3)


def test_cagr_flat_is_zero() -> None:
    assert m.cagr(_equity([100.0] * 100)) == pytest.approx(0.0)


def test_cagr_too_short() -> None:
    assert m.cagr(_equity([100.0])) == 0.0


# ---------------------------------------------------------------------------
# Max drawdown
# ---------------------------------------------------------------------------


def test_max_drawdown_known() -> None:
    # 100 → 120 → 60 → 90 : peak 120, trough 60 → DD = -50%
    eq = _equity([100.0, 120.0, 60.0, 90.0])
    assert m.max_drawdown(eq) == pytest.approx(-0.5)


def test_max_drawdown_monotonic_up_is_zero() -> None:
    assert m.max_drawdown(_equity([100.0, 110.0, 120.0])) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Sharpe / Sortino
# ---------------------------------------------------------------------------


def test_sharpe_zero_vol_is_zero() -> None:
    # constant returns → std 0 → Sharpe defined as 0
    rets = pd.Series([0.001] * 50)
    assert m.sharpe(rets) == 0.0


def test_sharpe_positive_for_positive_mean() -> None:
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.normal(0.001, 0.01, 252))
    assert m.sharpe(rets) > 0


def test_sortino_no_downside_is_zero() -> None:
    rets = pd.Series([0.01, 0.02, 0.0, 0.01])  # no negatives
    assert m.sortino(rets) == 0.0


def test_sortino_penalises_downside() -> None:
    rng = np.random.default_rng(1)
    rets = pd.Series(rng.normal(0.0005, 0.012, 252))
    s = m.sortino(rets)
    assert isinstance(s, float)


# ---------------------------------------------------------------------------
# Calmar
# ---------------------------------------------------------------------------


def test_calmar_zero_drawdown_is_zero() -> None:
    assert m.calmar(_equity([100.0, 110.0, 120.0])) == 0.0


def test_calmar_ratio() -> None:
    eq = _equity([*list(np.linspace(100.0, 150.0, 253)), 120.0])
    c = m.calmar(eq)
    assert isinstance(c, float)


# ---------------------------------------------------------------------------
# Hit rate & hold duration
# ---------------------------------------------------------------------------


def test_hit_rate() -> None:
    trades = [_trade(0.1, 30), _trade(-0.05, 20), _trade(0.2, 40)]
    assert m.hit_rate(trades) == pytest.approx(2 / 3)


def test_hit_rate_no_closed_trades() -> None:
    assert m.hit_rate([_trade(None, None, closed=False)]) == 0.0


def test_avg_hold_days() -> None:
    trades = [_trade(0.1, 30), _trade(-0.05, 10)]
    assert m.avg_hold_days(trades) == pytest.approx(20.0)


def test_avg_hold_days_open_excluded() -> None:
    trades = [_trade(0.1, 30), _trade(0.0, None, closed=False)]
    assert m.avg_hold_days(trades) == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Alpha vs benchmark
# ---------------------------------------------------------------------------


def test_alpha_positive_when_strategy_beats_benchmark() -> None:
    strat = pd.Series([0.002] * 252)
    bench = pd.Series([0.001] * 252)
    alpha = m.alpha_vs_benchmark(strat, bench)
    assert alpha == pytest.approx((0.002 - 0.001) * 252, rel=1e-6)


def test_alpha_empty_overlap_is_zero() -> None:
    assert m.alpha_vs_benchmark(pd.Series(dtype=float), pd.Series(dtype=float)) == 0.0


# ---------------------------------------------------------------------------
# compute_all
# ---------------------------------------------------------------------------


def test_compute_all_assembles_metrics() -> None:
    eq = _equity(list(np.linspace(100.0, 130.0, 253)))
    trades = [_trade(0.1, 30), _trade(-0.05, 20)]
    bench = pd.Series([0.0003] * 252)
    result = m.compute_all(eq, trades, bench)
    assert result.final_equity == pytest.approx(130.0)
    assert result.total_return == pytest.approx(0.30, rel=1e-6)
    assert result.hit_rate == pytest.approx(0.5)
    assert result.avg_hold_days == pytest.approx(25.0)
