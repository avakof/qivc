"""
Walk-forward backtest harness.

`run_backtest` walks rebalance dates from `start` to `end`. On each date it asks
a `screen_fn(as_of) -> list[ticker]` for the names to hold (equal-weight), builds
a target-percent order matrix, and simulates the portfolio with vectorbt
(`Portfolio.from_orders`) applying slippage + commission. Metrics are computed by
`qivc.backtest.metrics` from the resulting equity curve and trade list.

Dependency injection keeps it deterministic and testable:
  - `screen_fn`     — defaults to the real PIT screen; tests inject a stub.
  - `price_data`    — a (dates x tickers) close-price DataFrame; tests pass a
                      synthetic frame, production fetches from yfinance.
  - `benchmark`     — IWN (Russell 2000 Value) daily close for alpha; optional.

Reproducibility: given identical `price_data` and `screen_fn` outputs, the
simulation is deterministic (a fixed `seed` is passed to vectorbt), so the same
inputs always yield the same metrics.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd

from qivc.backtest import metrics as metrics_mod
from qivc.backtest.pit import SECTOR_MEDIAN_LOOKAHEAD_NOTE
from qivc.schemas import BacktestResult, BacktestTrade

log = logging.getLogger(__name__)

ScreenFn = Callable[[date], list[str]]
_SEED = 42


def _rebalance_dates(index: pd.DatetimeIndex, freq: Literal["W", "M"]) -> list[pd.Timestamp]:
    """Return the first trading day of each week/month within the price index."""
    rule = "W-MON" if freq == "W" else "MS"
    # Group the index by the resample bucket; take the first available trading day.
    grouped = pd.Series(index, index=index).resample(rule).first().dropna()
    return [pd.Timestamp(d) for d in grouped.to_numpy()]


def _build_target_weights(
    price: pd.DataFrame,
    rebalance_dates: list[pd.Timestamp],
    holdings_by_date: dict[pd.Timestamp, list[str]],
) -> pd.DataFrame:
    """
    Build a target-percent size matrix aligned to `price`.

    On each rebalance date, held tickers get equal weight (1/N) and everything
    else gets 0.0 (forcing an exit). Non-rebalance days are NaN (no order).
    """
    weights = pd.DataFrame(np.nan, index=price.index, columns=price.columns)
    for d in rebalance_dates:
        if d not in weights.index:
            continue
        held = [t for t in holdings_by_date.get(d, []) if t in weights.columns]
        weights.loc[d, :] = 0.0
        if held:
            w = 1.0 / len(held)
            for t in held:
                weights.loc[d, t] = w
    return weights


def _extract_trades(
    price: pd.DataFrame,
    weights: pd.DataFrame,
) -> list[BacktestTrade]:
    """
    Derive round-trip trades from the target-weight matrix: a position opens when
    a ticker's weight goes from 0/NaN to >0, and closes when it returns to 0.
    Entry/exit prices are the close on those dates.
    """
    trades: list[BacktestTrade] = []
    rebal_rows = weights.dropna(how="all")
    for ticker in price.columns:
        series = rebal_rows[ticker]
        open_date: pd.Timestamp | None = None
        open_price = 0.0
        prev_held = False
        for d, w in series.items():
            held = bool(w and w > 0)
            if held and not prev_held:
                open_date = pd.Timestamp(d)
                open_price = float(price.loc[d, ticker])
            elif not held and prev_held and open_date is not None:
                exit_price = float(price.loc[d, ticker])
                ret = (exit_price / open_price - 1.0) if open_price else None
                trades.append(
                    BacktestTrade(
                        ticker=ticker,
                        entry_date=open_date.date(),
                        exit_date=pd.Timestamp(d).date(),
                        entry_price=open_price,
                        exit_price=exit_price,
                        return_pct=ret,
                        hold_days=(pd.Timestamp(d) - open_date).days,
                    )
                )
                open_date = None
            prev_held = held
        # Still open at the end
        if prev_held and open_date is not None:
            last_price = float(price[ticker].iloc[-1])
            ret = (last_price / open_price - 1.0) if open_price else None
            trades.append(
                BacktestTrade(
                    ticker=ticker,
                    entry_date=open_date.date(),
                    exit_date=None,
                    entry_price=open_price,
                    exit_price=None,
                    return_pct=ret,
                    hold_days=None,
                )
            )
    return trades


def run_backtest(
    start: date,
    end: date,
    rebalance_freq: Literal["W", "M"] = "M",
    initial_capital: float = 1_000_000,
    slippage_bps: float = 5,
    commission_bps: float = 1,
    *,
    screen_fn: ScreenFn,
    price_data: pd.DataFrame,
    benchmark: pd.Series | None = None,
    backtest_id: str = "backtest",
) -> BacktestResult:
    """
    Run a walk-forward backtest.

    Parameters
    ----------
    screen_fn:
        ``screen_fn(as_of) -> [tickers]`` — the names to hold as of a rebalance
        date (uses only PIT data; injected for testability).
    price_data:
        Close prices, index = trading dates, columns = tickers.
    benchmark:
        Optional IWN daily close series for the alpha metric.
    """
    import vectorbt as vbt

    price = price_data.sort_index()
    price.index = pd.DatetimeIndex(price.index)
    price = price.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    if price.empty:
        raise ValueError("price_data has no rows in the requested [start, end] range")

    rebal_dates = _rebalance_dates(price.index, rebalance_freq)
    holdings_by_date: dict[pd.Timestamp, list[str]] = {}
    for d in rebal_dates:
        holdings_by_date[d] = screen_fn(pd.Timestamp(d).date())

    weights = _build_target_weights(price, rebal_dates, holdings_by_date)

    pf = vbt.Portfolio.from_orders(
        close=price,
        size=weights,
        size_type="targetpercent",
        fees=commission_bps / 10_000.0,
        slippage=slippage_bps / 10_000.0,
        init_cash=initial_capital,
        cash_sharing=True,
        group_by=True,
        call_seq="auto",
        freq="1D",
        seed=_SEED,
    )

    equity = pf.value()
    if isinstance(equity, pd.DataFrame):
        equity = equity.iloc[:, 0]
    equity = pd.Series(equity, index=price.index, dtype=float)

    trades = _extract_trades(price, weights)

    bench_returns: pd.Series | None = None
    if benchmark is not None:
        bench = pd.Series(benchmark, dtype=float).sort_index()
        bench.index = pd.DatetimeIndex(bench.index)
        bench = bench.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        bench_returns = bench.pct_change().dropna()

    metrics = metrics_mod.compute_all(equity, trades, bench_returns)

    notes = [SECTOR_MEDIAN_LOOKAHEAD_NOTE]
    if benchmark is None:
        notes.append("No benchmark provided; alpha_vs_iwn is 0.0.")

    equity_curve: list[tuple[date, float]] = [
        (pd.Timestamp(idx).date(), float(val)) for idx, val in equity.items()
    ]

    return BacktestResult(
        backtest_id=backtest_id,
        start=start,
        end=end,
        rebalance_freq=rebalance_freq,
        initial_capital=initial_capital,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        metrics=metrics,
        equity_curve=equity_curve,
        trades=trades,
        notes=notes,
    )
