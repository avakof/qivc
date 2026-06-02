#!/usr/bin/env python
"""
Run the full-QIVC 2025 backtest (Task 10).

    uv run python scripts/run_2025_backtest.py

All external data is cached under data/backtests/_cache_2025/ on first run, so
re-runs are byte-identical (reproducibility anchor). Outputs land in
data/backtests/2025_full_qivc/ : equity_curve.csv, trades.csv, holdings.csv,
metrics.json (with mandatory sample_size_warning), dossier_YYYY-MM.md.

This is ONE observation from a sample of one — see the sample_size_warning and
BACKTEST_REVIEW.md. Primary value: bug-finding + qualitative name review.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path

import httpx
import pandas as pd

from qivc.backtest import engine, providers
from qivc.backtest.pit_regime import regime_as_of
from qivc.backtest.signal import SignalResult, clusters_as_of, qivc_signal_as_of
from qivc.backtest.universe import UNIVERSE_AS_OF, iwm_sector_map, iwm_tickers
from qivc.config import Settings
from qivc.data.edgar_client import EdgarClient
from qivc.schemas import MarketRegime

log = logging.getLogger("backtest2025")

START = _dt.date(2025, 1, 2)
END = _dt.date(2025, 12, 31)
PRICE_START = _dt.date(2024, 10, 1)  # warmup for 20d ADV
PRICE_END = _dt.date(2026, 1, 3)
INIT_CASH = 1_000_000.0
SLIPPAGE_BPS = 5.0
COMMISSION_BPS = 1.0
RUN_ID = "2025_full_qivc"


def _regime_cached(
    cache: providers.BacktestCache, as_of: _dt.date, client: httpx.Client
) -> MarketRegime:
    key = f"regime_{as_of.isoformat()}"
    rec = cache.get_json(key)
    if rec is not None:
        return MarketRegime(**rec)
    reg = regime_as_of(as_of, client=client)
    cache.put_json(key, reg.model_dump(mode="json"))
    return reg


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    db = settings.db_path
    universe = iwm_tickers()
    sector_map = iwm_sector_map()
    cache = providers.BacktestCache(Path("data/backtests/_cache_2025"))

    # ---- Prices: benchmarks + rates first (to get the trading calendar) ----
    log.info("Fetching benchmark/rate prices ...")
    providers.build_price_cache(
        providers.BacktestCache(cache.dir / "_bench"),
        ["IWN", "SPY", "^IRX"],
        PRICE_START,
        PRICE_END,
    )
    bench_close, _ = providers.load_prices(providers.BacktestCache(cache.dir / "_bench"))
    calendar = pd.DatetimeIndex(bench_close.index)
    rebalance = engine.first_trading_days(calendar, START, END)
    log.info("Rebalance dates (%d): %s", len(rebalance), [r.date().isoformat() for r in rebalance])

    # ---- PIT regime per rebalance (cached) ----
    with httpx.Client(timeout=30.0) as hc:
        regimes = {r.date(): _regime_cached(cache, r.date(), hc) for r in rebalance}
    log.info("Regimes: %s", {d.isoformat(): rg.regime for d, rg in regimes.items()})

    # ---- Pass 1: clustered tickers per rebalance (bulk store only) ----
    clusters_by_rb = {
        r.date(): clusters_as_of(r.date(), db_path=db, universe=universe) for r in rebalance
    }
    clustered = sorted({t for cl in clusters_by_rb.values() for t in cl})
    log.info("Clustered tickers in 2025 (%d): %s", len(clustered), clustered)

    # ---- Cache: prices, info, fundamentals for clustered tickers ----
    if clustered:
        providers.build_price_cache(cache, clustered, PRICE_START, PRICE_END)
        info = providers.fetch_info_cache(cache, clustered)
        client = EdgarClient(
            settings.edgar_user_agent, rate_limit_rps=settings.edgar_rate_limit_rps
        )
        ticker_dates = [(t, r.date()) for r in rebalance for t in clusters_by_rb[r.date()]]
        log.info("Fetching PIT fundamentals for %d (ticker, date) pairs ...", len(ticker_dates))
        providers.build_fundamentals_cache(cache, client, ticker_dates)
        close, vol = providers.load_prices(cache)
    else:
        info, close, vol = {}, pd.DataFrame(), pd.DataFrame()

    fund_provider = providers.make_fundamentals_provider(cache)
    liq_provider = providers.make_liquidity_provider(close, vol, info)
    ind_provider = providers.make_industry_provider(info)

    # ---- Pass 2: full signal per rebalance ----
    signals: dict[_dt.date, SignalResult] = {}
    holdings_by_date: dict[pd.Timestamp, list[tuple[str, float]]] = {}
    out_dir = Path("data/backtests") / RUN_ID
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in rebalance:
        d = r.date()
        sig = qivc_signal_as_of(
            d,
            db_path=db,
            universe=universe,
            sector_map=sector_map,
            regime=regimes[d],
            fundamentals_provider=fund_provider,
            liquidity_provider=liq_provider,
            industry_provider=ind_provider,
        )
        signals[d] = sig
        holdings_by_date[r] = [(h.ticker, h.weight) for h in sig.holdings]
        sanity = engine.check_dossier_sanity(sig)
        (out_dir / f"dossier_{d.strftime('%Y-%m')}.md").write_text(
            engine.render_dossier(sig, sanity)
        )
        bad = [n for n, ok, _ in sanity if not ok]
        log.info(
            "  %s: %d holds, cash %.0f%%, sanity %s",
            d,
            len(sig.holdings),
            sig.cash_weight * 100,
            "PASS" if not bad else f"FAIL {bad}",
        )

    # ---- Build price matrix (held tickers + CASH) and simulate ----
    held = sorted({t for hs in holdings_by_date.values() for t, _ in hs})
    period = calendar[(calendar >= pd.Timestamp(START)) & (calendar <= pd.Timestamp(END))]
    irx = bench_close["^IRX"] if "^IRX" in bench_close else pd.Series(0.0, index=calendar)
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
        price,
        weights,
        init_cash=INIT_CASH,
        slippage_bps=SLIPPAGE_BPS,
        commission_bps=COMMISSION_BPS,
    )
    trades = engine.extract_trades(price, weights)
    snaps = engine.holdings_snapshots(weights, [r for r in rebalance if r in period])

    iwn = bench_close["IWN"].reindex(period).ffill()
    spy = bench_close["SPY"].reindex(period).ffill()
    monthly = equity.resample("ME").last().pct_change().dropna()
    metrics = engine.compute_metrics(equity, monthly, trades, iwn, spy, signals, snaps)

    art = engine.BacktestArtifacts(
        equity=equity,
        monthly_returns=monthly,
        trades=trades,
        holdings_snapshots=snaps,
        signals=signals,
        metrics=metrics,
        diagnostics={
            "universe_as_of": UNIVERSE_AS_OF,
            "universe_size": len(universe),
            "clustered_tickers_2025": clustered,
            "seed": 42,
            "init_cash": INIT_CASH,
        },
    )
    meta = {
        "backtest_id": RUN_ID,
        "start": str(START),
        "end": str(END),
        "rebalance": "first trading day of each month",
        "universe": "IWM (Russell 2000)",
        "universe_as_of": UNIVERSE_AS_OF,
        "slippage_bps": SLIPPAGE_BPS,
        "commission_bps": COMMISSION_BPS,
        "initial_capital": INIT_CASH,
    }
    engine.write_outputs(out_dir, art, meta)
    log.info("\n=== DONE === outputs in %s", out_dir)
    log.info(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
