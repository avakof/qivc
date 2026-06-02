"""
v3.0 parameterised multi-config backtest grid harness (Phase 2).

``run_backtest_grid(year, configs, ...)`` runs one backtest per GridConfig for a
single year. The expensive PIT factor data (insider activity + EDGAR
fundamentals) is **config-independent** at each rebalance date, so it is assembled
**once per year and shared** across all configs; only the cheap, pure portfolio
construction + vectorbt sim varies per config. Outputs (the same 5 files as
Task 10) land in ``data/backtests/v3_<year>_<config-label>/``. Reproducible:
cached data + seed=42 → byte-identical.
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from qivc.backtest import engine, providers
from qivc.backtest.composite_scorer import ScoredStock, score_universe
from qivc.backtest.pit_regime import regime_as_of
from qivc.backtest.portfolio_constructor import GridConfig, Position, construct_portfolio
from qivc.backtest.universe import iwm_sector_map, iwm_tickers
from qivc.backtest.v3_factors import assemble_factors, opportunistic_insider_raw
from qivc.data.edgar_client import EdgarClient
from qivc.schemas import MarketRegime

log = logging.getLogger(__name__)

_EXCLUDE_SECTORS = {"Financials", "Real Estate"}
_PRICE_WARMUP = _dt.timedelta(days=95)
_SEED = 42


def _regime_cached(
    cache: providers.BacktestCache, as_of: _dt.date, hc: httpx.Client
) -> MarketRegime:
    rec = cache.get_json(f"regime_{as_of.isoformat()}")
    if rec is not None:
        return MarketRegime(**rec)
    reg = regime_as_of(as_of, client=hc)
    cache.put_json(f"regime_{as_of.isoformat()}", reg.model_dump(mode="json"))
    return reg


def _v3_dossier(
    year_label: str, as_of: _dt.date, regime: MarketRegime, positions: list[Position], n_scored: int
) -> str:
    lines = [
        "> Research output, not investment advice. v3.0 composite backtest dossier "
        "(point-in-time). Caveats: BACKTEST_LIMITATIONS.md / BACKTEST_REVIEW_V3_GRID.md.",
        "",
        f"# QIVC v3.0 Dossier — {as_of.isoformat()} ({year_label})",
        "",
        f"**Regime:** {regime.regime}  **Scored (insider-active):** {n_scored}  "
        f"**Held:** {len(positions)}  **Equity:** {sum(p.weight for p in positions) * 100:.0f}%",
        "",
    ]
    if positions:
        lines += ["| Ticker | Sector | Composite | Weight % | Entry |", "|---|---|---|---|---|"]
        for p in sorted(positions, key=lambda p: -p.weight):
            lines.append(
                f"| {p.ticker} | {p.sector} | {p.composite:.3f} | "
                f"{p.weight * 100:.1f} | {p.entry_date.isoformat()} |"
            )
    else:
        lines.append("_No holdings this month (100% T-bills)._")
    return "\n".join(lines) + "\n"


def run_backtest_grid(
    year: int,
    configs: list[GridConfig],
    *,
    db_path: str,
    edgar_user_agent: str,
    edgar_rps: int = 8,
    months: list[int] | None = None,  # dry-run: restrict to these months
    out_root: str = "data/backtests",
    cache_root: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Run all *configs* for *year*; returns {config_label: metrics}."""
    start = _dt.date(year, 1, 1)
    end = _dt.date(year, 12, 31)
    cache = providers.BacktestCache(Path(cache_root or f"{out_root}/_cache_v3_{year}"))
    sector_map = iwm_sector_map()
    universe = {t for t in iwm_tickers() if sector_map.get(t) not in _EXCLUDE_SECTORS}

    # ---- Benchmarks + calendar ----
    bench_cache = providers.BacktestCache(cache.dir / "_bench")
    providers.build_price_cache(
        bench_cache, ["IWN", "SPY", "^IRX"], start - _PRICE_WARMUP, end + _dt.timedelta(days=3)
    )
    bench_close, _ = providers.load_prices(bench_cache)
    calendar = pd.DatetimeIndex(bench_close.index)
    rebalance = engine.first_trading_days(calendar, start, end)
    if months is not None:
        rebalance = [r for r in rebalance if r.month in months]
    log.info("v3 grid %d: %d rebalance dates, %d configs", year, len(rebalance), len(configs))

    # ---- PIT regime per rebalance (cached) ----
    with httpx.Client(timeout=30.0) as hc:
        regimes = {r.date(): _regime_cached(cache, r.date(), hc) for r in rebalance}

    # ---- Pass A: insider-active universe per rebalance -> data to fetch ----
    insider_by_rb = {
        r.date(): opportunistic_insider_raw(db_path, universe, r.date()) for r in rebalance
    }
    active = sorted({t for d in insider_by_rb.values() for t in d})
    log.info("v3 grid %d: %d insider-active tickers to fetch", year, len(active))

    # ---- Cache prices + fundamentals for the active set ----
    if active:
        providers.build_price_cache(
            cache, active, start - _PRICE_WARMUP, end + _dt.timedelta(days=3)
        )
        client = EdgarClient(edgar_user_agent, rate_limit_rps=edgar_rps)
        ticker_dates = [(t, r.date()) for r in rebalance for t in insider_by_rb[r.date()]]
        providers.build_fundamentals_cache(cache, client, ticker_dates)
        close, _vol = providers.load_prices(cache)
    else:
        close = pd.DataFrame()
    fund_provider = providers.make_fundamentals_provider(cache)

    # ---- Pass B: assemble factors + score per rebalance (shared across configs) ----
    scored_by_rb: dict[_dt.date, list[ScoredStock]] = {}
    for r in rebalance:
        factors = assemble_factors(
            r.date(),
            db_path=db_path,
            universe=universe,
            sector_map=sector_map,
            fundamentals_provider=fund_provider,
        )
        scored_by_rb[r.date()] = score_universe(factors)

    # ---- Pass C: per-config construction + sim + outputs ----
    period = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    if months is not None:
        period = period[period.month.isin(months)]
    irx = bench_close["^IRX"] if "^IRX" in bench_close else pd.Series(0.0, index=calendar)
    iwn = bench_close["IWN"].reindex(period).ffill()
    spy = bench_close["SPY"].reindex(period).ffill()

    results: dict[str, dict[str, Any]] = {}
    for cfg in configs:
        results[cfg.label] = _run_one_config(
            year,
            cfg,
            rebalance,
            period,
            scored_by_rb,
            regimes,
            close,
            irx,
            iwn,
            spy,
            sector_map,
            Path(out_root),
        )
    return results


def _run_one_config(
    year: int,
    cfg: GridConfig,
    rebalance: list[pd.Timestamp],
    period: pd.DatetimeIndex,
    scored_by_rb: dict[_dt.date, list[ScoredStock]],
    regimes: dict[_dt.date, MarketRegime],
    close: pd.DataFrame,
    irx: pd.Series,
    iwn: pd.Series,
    spy: pd.Series,
    sector_map: dict[str, str],
    out_root: Path,
) -> dict[str, Any]:
    out_dir = out_root / f"v3_{year}_{cfg.label}"
    out_dir.mkdir(parents=True, exist_ok=True)

    prev: list[Position] = []
    holdings_by_date: dict[pd.Timestamp, list[tuple[str, float]]] = {}
    n_held_by_month: list[int] = []
    for r in rebalance:
        d = r.date()
        positions = construct_portfolio(scored_by_rb[d], prev, d, cfg, regimes[d])
        prev = positions
        holdings_by_date[r] = [(p.ticker, p.weight) for p in positions]
        n_held_by_month.append(len(positions))
        (out_dir / f"dossier_{d.strftime('%Y-%m')}.md").write_text(
            _v3_dossier(str(year), d, regimes[d], positions, len(scored_by_rb[d]))
        )

    held = sorted({t for hs in holdings_by_date.values() for t, _ in hs})
    price = pd.DataFrame(index=period)
    for t in held:
        if t in close.columns:
            price[t] = close[t].reindex(period).ffill().bfill()
    price[engine._CASH] = engine.build_cash_series(period, irx)
    price = price.dropna(axis=1, how="all")

    weights = engine.build_target_weights(
        price, [r for r in rebalance if r in period], holdings_by_date
    )
    equity = engine.simulate(
        price, weights, init_cash=1_000_000.0, slippage_bps=5.0, commission_bps=1.0
    )
    trades = engine.extract_trades(price, weights)
    snaps = engine.holdings_snapshots(weights, [r for r in rebalance if r in period])
    monthly = equity.resample("ME").last().pct_change().dropna()
    metrics = engine.compute_metrics(equity, monthly, trades, iwn, spy, {}, snaps)
    # v3 diagnostics (compute_metrics' signal-derived fields are 0 with empty signals)
    metrics["months_with_zero_candidates"] = sum(1 for n in n_held_by_month if n == 0)
    metrics["average_position_count"] = round(sum(n_held_by_month) / len(n_held_by_month), 3)
    metrics["config"] = {
        "n": cfg.n,
        "entry_threshold_pct": cfg.entry_threshold_pct,
        "holding_period_days": cfg.holding_period_days,
    }

    art = engine.BacktestArtifacts(
        equity=equity,
        monthly_returns=monthly,
        trades=trades,
        holdings_snapshots=snaps,
        signals={},
        metrics=metrics,
        diagnostics={
            "year": year,
            "config": cfg.label,
            "seed": _SEED,
            "framework": "v3.0 composite top-N",
        },
    )
    meta = {
        "backtest_id": f"v3_{year}_{cfg.label}",
        "year": year,
        "framework": "v3.0",
        "config": cfg.label,
        "rebalance": "first trading day of each month",
    }
    engine.write_outputs(out_dir, art, meta)
    log.info(
        "  %s: trades=%d total_return=%.2f%% avg_held=%.1f",
        cfg.label,
        metrics["total_trades"],
        metrics["total_return_pct"],
        metrics["average_position_count"],
    )
    return metrics
