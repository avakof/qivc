"""Backtest output writers — equity_curve.csv, metrics.json, trades.csv."""

from __future__ import annotations

import csv
from pathlib import Path

from qivc.schemas import BacktestResult


def write_backtest_outputs(result: BacktestResult, out_dir: Path) -> None:
    """Write equity_curve.csv, metrics.json, and trades.csv into *out_dir*."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # equity_curve.csv
    with open(out_dir / "equity_curve.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "equity"])
        for d, v in result.equity_curve:
            writer.writerow([d.isoformat(), f"{v:.6f}"])

    # metrics.json (includes run parameters + methodology notes)
    metrics_payload = {
        "backtest_id": result.backtest_id,
        "start": result.start.isoformat(),
        "end": result.end.isoformat(),
        "rebalance_freq": result.rebalance_freq,
        "initial_capital": result.initial_capital,
        "slippage_bps": result.slippage_bps,
        "commission_bps": result.commission_bps,
        "metrics": result.metrics.model_dump(),
        "notes": result.notes,
    }
    import json

    (out_dir / "metrics.json").write_text(json.dumps(metrics_payload, indent=2))

    # trades.csv
    with open(out_dir / "trades.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "ticker",
                "entry_date",
                "exit_date",
                "entry_price",
                "exit_price",
                "return_pct",
                "hold_days",
            ]
        )
        for t in result.trades:
            writer.writerow(
                [
                    t.ticker,
                    t.entry_date.isoformat(),
                    t.exit_date.isoformat() if t.exit_date else "",
                    f"{t.entry_price:.6f}",
                    f"{t.exit_price:.6f}" if t.exit_price is not None else "",
                    f"{t.return_pct:.6f}" if t.return_pct is not None else "",
                    str(t.hold_days) if t.hold_days is not None else "",
                ]
            )
