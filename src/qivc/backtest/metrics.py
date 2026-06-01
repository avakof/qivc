"""
Backtest performance metrics — pure functions over an equity curve and trades.

All functions operate on plain numpy/pandas inputs so they are deterministic and
directly unit-testable, independent of the portfolio-simulation engine.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qivc.schemas import BacktestMetrics, BacktestTrade

_TRADING_DAYS = 252


def daily_returns(equity: pd.Series) -> pd.Series:
    """Simple daily returns from an equity-value series."""
    return equity.pct_change().dropna()


def cagr(equity: pd.Series, periods_per_year: int = _TRADING_DAYS) -> float:
    """Compound annual growth rate from first to last equity value."""
    if len(equity) < 2:
        return 0.0
    start_val = float(equity.iloc[0])
    end_val = float(equity.iloc[-1])
    if start_val <= 0:
        return 0.0
    n_years = (len(equity) - 1) / periods_per_year
    if n_years <= 0:
        return 0.0
    return float((end_val / start_val) ** (1.0 / n_years) - 1.0)


def sharpe(returns: pd.Series, rf: float = 0.0, periods_per_year: int = _TRADING_DAYS) -> float:
    """Annualised Sharpe ratio. 0.0 if volatility is zero."""
    if len(returns) == 0:
        return 0.0
    excess = returns - rf / periods_per_year
    std = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0
    if std == 0.0:
        return 0.0
    return float(float(excess.mean()) / std * np.sqrt(periods_per_year))


def sortino(returns: pd.Series, rf: float = 0.0, periods_per_year: int = _TRADING_DAYS) -> float:
    """Annualised Sortino ratio (downside deviation). 0.0 if no downside."""
    if len(returns) == 0:
        return 0.0
    excess = returns - rf / periods_per_year
    downside = excess[excess < 0]
    if len(downside) == 0:
        return 0.0
    dd = float(np.sqrt((downside**2).mean()))
    if dd == 0.0:
        return 0.0
    return float(float(excess.mean()) / dd * np.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a negative fraction (e.g. -0.20)."""
    if len(equity) == 0:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def calmar(equity: pd.Series, periods_per_year: int = _TRADING_DAYS) -> float:
    """CAGR / |max drawdown|. 0.0 if no drawdown."""
    mdd = max_drawdown(equity)
    if mdd == 0.0:
        return 0.0
    return cagr(equity, periods_per_year) / abs(mdd)


def hit_rate(trades: list[BacktestTrade]) -> float:
    """Fraction of CLOSED trades with a positive return."""
    closed = [t for t in trades if t.return_pct is not None]
    if not closed:
        return 0.0
    wins = sum(1 for t in closed if (t.return_pct or 0.0) > 0)
    return wins / len(closed)


def avg_hold_days(trades: list[BacktestTrade]) -> float:
    """Average hold duration in days over CLOSED trades."""
    closed = [t.hold_days for t in trades if t.hold_days is not None]
    if not closed:
        return 0.0
    return float(np.mean(closed))


def alpha_vs_benchmark(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    periods_per_year: int = _TRADING_DAYS,
) -> float:
    """
    Annualised alpha: difference between the strategy's and the benchmark's
    annualised mean return over the overlapping dates. (A simple excess-return
    alpha; a full factor regression is out of scope.)
    """
    aligned = pd.concat([strategy_returns, benchmark_returns], axis=1, join="inner").dropna()
    if aligned.empty:
        return 0.0
    strat_ann = float(aligned.iloc[:, 0].mean()) * periods_per_year
    bench_ann = float(aligned.iloc[:, 1].mean()) * periods_per_year
    return strat_ann - bench_ann


def compute_all(
    equity: pd.Series,
    trades: list[BacktestTrade],
    benchmark_returns: pd.Series | None = None,
) -> BacktestMetrics:
    """Assemble the full BacktestMetrics from an equity curve, trades, and benchmark."""
    rets = daily_returns(equity)
    bench = benchmark_returns if benchmark_returns is not None else pd.Series(dtype=float)
    start_val = float(equity.iloc[0]) if len(equity) else 0.0
    end_val = float(equity.iloc[-1]) if len(equity) else 0.0
    total_return = (end_val / start_val - 1.0) if start_val > 0 else 0.0

    return BacktestMetrics(
        cagr=cagr(equity),
        sharpe=sharpe(rets),
        sortino=sortino(rets),
        max_drawdown=max_drawdown(equity),
        calmar=calmar(equity),
        hit_rate=hit_rate(trades),
        avg_hold_days=avg_hold_days(trades),
        alpha_vs_iwn=alpha_vs_benchmark(rets, bench) if len(bench) else 0.0,
        total_return=total_return,
        final_equity=end_val,
    )
