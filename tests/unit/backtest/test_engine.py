"""Unit tests for the backtest engine (Task 10) — calendar, weights, metrics."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from qivc.backtest import engine
from qivc.backtest.signal import SignalResult
from qivc.schemas import MarketRegime

_REG = MarketRegime(
    vix_60d_sma=18.0,
    credit_spread_bps=150.0,
    yield_curve_bps=30.0,
    value_growth_12m=0.0,
    regime="risk-on",
)


def _cal() -> pd.DatetimeIndex:
    return pd.bdate_range("2025-01-01", "2025-03-31")


def test_first_trading_days_picks_first_of_month() -> None:
    rb = engine.first_trading_days(_cal(), dt.date(2025, 1, 1), dt.date(2025, 3, 31))
    months = sorted({(t.year, t.month) for t in rb})
    assert months == [(2025, 1), (2025, 2), (2025, 3)]
    assert rb[0] == pd.Timestamp("2025-01-01")  # first business day


def test_cash_series_compounds_at_irx() -> None:
    idx = _cal()
    irx = pd.Series(5.0, index=idx)  # 5% annual
    cash = engine.build_cash_series(idx, irx)
    assert cash.iloc[0] > 1.0
    assert cash.is_monotonic_increasing
    # ~ (1 + 0.05/252)^n
    expected = (1 + 0.05 / 252) ** (len(idx))
    assert abs(cash.iloc[-1] - expected) < 1e-6


def test_target_weights_include_cash_residual() -> None:
    idx = _cal()
    price = pd.DataFrame(1.0, index=idx, columns=["AAA", "BBB", engine._CASH])
    rb = [idx[0]]
    w = engine.build_target_weights(price, rb, {idx[0]: [("AAA", 0.05), ("BBB", 0.03)]})
    row = w.loc[idx[0]]
    assert row["AAA"] == 0.05
    assert row["BBB"] == 0.03
    assert abs(row[engine._CASH] - 0.92) < 1e-9  # residual to cash
    assert pd.isna(w.iloc[1]["AAA"])  # no order on non-rebalance days


def test_metrics_degenerate_all_cash_flagged() -> None:
    idx = _cal()
    # all-cash equity: smooth compounding, ~zero vol -> degenerate Sharpe.
    eq = pd.Series((1 + 0.04 / 252) ** pd.RangeIndex(len(idx)), index=idx) * 1_000_000
    monthly = eq.resample("ME").last().pct_change().dropna()
    bench = pd.Series(range(100, 100 + len(idx)), index=idx, dtype=float)
    trades = pd.DataFrame(
        columns=[
            "ticker",
            "entry_date",
            "exit_date",
            "entry_price",
            "exit_price",
            "return_pct",
            "hold_days",
        ]
    )
    snaps = pd.DataFrame({engine._CASH: [1.0]}, index=[idx[0].date()])
    m = engine.compute_metrics(eq, monthly, trades, bench, bench, {}, snaps)
    assert m["total_trades"] == 0
    assert m["max_drawdown_pct"] == 0.0
    assert "DEGENERATE" in m["sharpe_caveat"]  # high Sharpe explained, not look-ahead
    assert m["deflated_sharpe_ratio"] is None


def test_extract_trades_roundtrip() -> None:
    idx = _cal()
    price = pd.DataFrame(index=idx)
    price["AAA"] = pd.Series(range(10, 10 + len(idx)), index=idx, dtype=float)
    price[engine._CASH] = 1.0
    w = pd.DataFrame(index=idx, columns=["AAA", engine._CASH], dtype=float)
    # held from day 5 to day 20, then exits
    w.iloc[5] = [0.1, 0.9]
    w.iloc[20] = [0.0, 1.0]
    trades = engine.extract_trades(price, w)
    assert list(trades["ticker"]) == ["AAA"]
    assert trades.iloc[0]["exit_date"] is not None
    assert trades.iloc[0]["return_pct"] is not None


def test_dossier_sanity_vacuous_on_zero_candidates() -> None:
    sig = SignalResult(
        as_of=dt.date(2025, 4, 1),
        regime=_REG,
        holdings=[],
        candidates=[],
        rejected_count=3,
        rejected_by_gate={"f_score": 2, "gp_a": 1},
        cash_weight=1.0,
        n_clustered_tickers=3,
        regime_blocked=False,
    )
    sanity = engine.check_dossier_sanity(sig)
    assert all(ok for _, ok, _ in sanity)  # all pass vacuously
    md = engine.render_dossier(sig, sanity)
    assert "No surviving candidates" in md
    assert "0 candidates is legitimate" in md
