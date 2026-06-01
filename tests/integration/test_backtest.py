"""
Integration test for the backtest harness on a small synthetic universe.

Verifies:
  - the harness runs end-to-end and produces an equity curve, trades, metrics;
  - results are reproducible (same inputs → identical numbers);
  - the screen_fn only ever receives as-of dates (no future leak by construction);
  - CSV/JSON outputs are written.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qivc.backtest.harness import run_backtest
from qivc.backtest.io import write_backtest_outputs
from qivc.schemas import BacktestResult


def _synthetic_prices() -> pd.DataFrame:
    """Deterministic, noisy-but-fixed prices for a 4-ticker, ~6-month universe."""
    idx = pd.bdate_range("2024-01-01", periods=130)
    rng = np.random.default_rng(7)
    n = len(idx)
    data = {}
    # Each ticker: geometric random walk with a fixed seed → deterministic
    for i, ticker in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        drift = 0.0006 - 0.0002 * i
        shocks = rng.normal(drift, 0.01, n)
        data[ticker] = 100.0 * np.exp(np.cumsum(shocks))
    return pd.DataFrame(data, index=idx)


def _benchmark(idx: pd.DatetimeIndex) -> pd.Series:
    rng = np.random.default_rng(99)
    shocks = rng.normal(0.0003, 0.008, len(idx))
    return pd.Series(100.0 * np.exp(np.cumsum(shocks)), index=idx)


def test_backtest_runs_end_to_end() -> None:
    price = _synthetic_prices()
    bench = _benchmark(pd.DatetimeIndex(price.index))

    seen_dates: list[date] = []

    def screen_fn(as_of: date) -> list[str]:
        seen_dates.append(as_of)
        # Deterministic rotation: hold the two "strongest" names by simple rule.
        return ["AAA", "BBB"]

    result = run_backtest(
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
        rebalance_freq="M",
        screen_fn=screen_fn,
        price_data=price,
        benchmark=bench,
        backtest_id="it-test",
    )

    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) > 0
    assert result.metrics.final_equity > 0
    assert result.equity_curve[0][1] > 0
    # Monthly over ~6 months → ~6 rebalance dates
    assert 4 <= len(seen_dates) <= 7
    # No date passed to the screen is beyond the backtest end
    assert all(d <= date(2024, 6, 30) for d in seen_dates)


def test_backtest_reproducible() -> None:
    price = _synthetic_prices()
    bench = _benchmark(pd.DatetimeIndex(price.index))

    def screen_fn(as_of: date) -> list[str]:
        return ["AAA", "CCC"]

    kwargs: dict[str, Any] = dict(
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
        rebalance_freq="M",
        screen_fn=screen_fn,
        price_data=price,
        benchmark=bench,
        backtest_id="repro",
    )
    r1 = run_backtest(**kwargs)
    r2 = run_backtest(**kwargs)

    assert r1.equity_curve == r2.equity_curve
    assert r1.metrics == r2.metrics
    assert r1.trades == r2.trades


def test_backtest_closed_trades_have_hold_and_returns() -> None:
    """Rotating holdings produce closed round-trips with hold_days and returns."""
    price = _synthetic_prices()

    # Alternate holdings month to month so positions actually close.
    toggle = {"n": 0}

    def screen_fn(as_of: date) -> list[str]:
        toggle["n"] += 1
        return ["AAA"] if toggle["n"] % 2 == 1 else ["BBB"]

    result = run_backtest(
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
        rebalance_freq="M",
        screen_fn=screen_fn,
        price_data=price,
        backtest_id="rotate",
    )
    closed = [t for t in result.trades if t.exit_date is not None]
    assert closed, "expected at least one closed round-trip"
    for t in closed:
        assert t.hold_days is not None and t.hold_days > 0
        assert t.return_pct is not None


def test_backtest_outputs_written(tmp_path: Path) -> None:
    price = _synthetic_prices()

    def screen_fn(as_of: date) -> list[str]:
        return ["AAA", "BBB"]

    result = run_backtest(
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
        rebalance_freq="M",
        screen_fn=screen_fn,
        price_data=price,
        backtest_id="out",
    )
    out_dir = tmp_path / "out"
    write_backtest_outputs(result, out_dir)

    assert (out_dir / "equity_curve.csv").exists()
    assert (out_dir / "metrics.json").exists()
    assert (out_dir / "trades.csv").exists()

    metrics = json.loads((out_dir / "metrics.json").read_text())
    assert metrics["backtest_id"] == "out"
    assert "metrics" in metrics and "cagr" in metrics["metrics"]
    assert any("look-ahead" in n for n in metrics["notes"])

    equity_lines = (out_dir / "equity_curve.csv").read_text().strip().splitlines()
    assert equity_lines[0] == "date,equity"
    assert len(equity_lines) > 1


def test_cli_backtest_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    """`qivc backtest` runs with mocked yfinance + PIT screen and writes outputs."""
    from types import SimpleNamespace
    from unittest import mock

    from typer.testing import CliRunner

    from qivc.cli import main as cli_main

    monkeypatch.setenv("QIVC_EDGAR_USER_AGENT", "Test User test@example.com")

    price = _synthetic_prices()
    universe = list(price.columns)
    # Build a yfinance-style frame: MultiIndex columns (field, ticker), incl. IWN.
    bench = _benchmark(pd.DatetimeIndex(price.index))
    close = price.copy()
    close["IWN"] = bench.to_numpy()
    yf_frame = pd.concat({"Close": close}, axis=1)  # columns: ("Close", ticker)

    monkeypatch.setattr("yfinance.download", lambda *a, **k: yf_frame)
    # Avoid live EDGAR: stub agents + the PIT quality screen.
    monkeypatch.setattr(
        cli_main,
        "build_agents",
        lambda s: SimpleNamespace(regime=SimpleNamespace(_client=mock.MagicMock())),
    )

    async def _fake_holdings(client: Any, uni: list[str], as_of: Any) -> list[str]:
        return ["AAA", "BBB"]

    monkeypatch.setattr(cli_main, "_pit_quality_holdings", _fake_holdings)

    runner = CliRunner()
    out_dir = tmp_path / "backtests"
    result = runner.invoke(
        cli_main.app,
        [
            "backtest",
            "--start",
            "2024-01-01",
            "--end",
            "2024-06-30",
            "--freq",
            "M",
            "--universe",
            ",".join(universe),
            "--output-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    run_dirs = list(out_dir.iterdir())
    assert len(run_dirs) == 1
    bt = run_dirs[0]
    assert (bt / "equity_curve.csv").exists()
    assert (bt / "metrics.json").exists()
    assert (bt / "trades.csv").exists()


def test_backtest_empty_range_raises() -> None:
    price = _synthetic_prices()

    def screen_fn(as_of: date) -> list[str]:
        return []

    import pytest

    with pytest.raises(ValueError):
        run_backtest(
            start=date(2030, 1, 1),
            end=date(2030, 2, 1),
            screen_fn=screen_fn,
            price_data=price,
            backtest_id="empty",
        )
