#!/usr/bin/env python
"""
Run the v3.0 18-config grid for one year and print the summary table.

    uv run python scripts/run_v3_grid.py 2025

18 configs = holding {30,90,180} x threshold {60,75,90} x N {5,10}. Factor data is
fetched+cached once per year (shared across configs); outputs land in
data/backtests/v3_<year>_<label>/. ONE OBSERVATION per (config, year) — see the
sample_size_warning in each metrics.json and BACKTEST_REVIEW_V3_GRID.md.
"""

from __future__ import annotations

import logging
import sys

from qivc.backtest.grid import run_backtest_grid
from qivc.backtest.portfolio_constructor import GridConfig
from qivc.config import Settings


def grid18() -> list[GridConfig]:
    return [
        GridConfig(n, thr, hold) for hold in (30, 90, 180) for thr in (60, 75, 90) for n in (5, 10)
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    s = Settings()
    configs = grid18()
    res = run_backtest_grid(
        year,
        configs,
        db_path=s.db_path,
        edgar_user_agent=s.edgar_user_agent,
        edgar_rps=s.edgar_rate_limit_rps,
    )

    cols = (
        "Trades",
        "Return%",
        "Sharpe",
        "MaxDD%",
        "Sortino",
        "AvgHeld",
        "Cash%",
        "ZeroMo",
        "SanFail",
    )
    print(f"\n{'Config':<18}" + "".join(f"{c:>9}" for c in cols))
    print("-" * (18 + 9 * len(cols)))
    rets = []
    for cfg in configs:
        m = res[cfg.label]
        rets.append(m["total_return_pct"])
        print(
            f"{cfg.label:<18}"
            f"{m['total_trades']:>9}{m['total_return_pct']:>9.2f}{m['sharpe']:>9.1f}"
            f"{m['max_drawdown_pct']:>9.2f}{m['sortino']:>9.2f}{m['average_position_count']:>9.2f}"
            f"{m['average_cash_pct']:>9.1f}{m['months_with_zero_candidates']:>9}"
            f"{m.get('dossier_sanity_failures', 0):>9}"
        )

    pos = sum(1 for r in rets if r > 0)
    srt = sorted(rets)
    median = srt[len(srt) // 2]
    print(
        f"\nAggregate: median return {median:.2f}% | range [{min(rets):.2f}, {max(rets):.2f}] | "
        f"{pos}/{len(rets)} positive"
    )
    # benchmark context
    any_m = res[configs[0].label]
    print(
        f"Benchmarks {year}: IWN {any_m['iwn_total_return_pct']:.1f}% | "
        f"SPY {any_m['spy_total_return_pct']:.1f}% (context)."
    )
    print("ALL RETURNS ARE ONE OBSERVATION; 8-15 trades/yr is below the significance floor.")


if __name__ == "__main__":
    main()
