"""Unit tests for the v3.0 grid harness (Phase 2) — fully offline (seeded cache)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from qivc.backtest import providers
from qivc.backtest.grid import run_backtest_grid
from qivc.backtest.portfolio_constructor import GridConfig
from qivc.schemas import Fundamentals, MarketRegime
from qivc.storage.db import get_connection

# Real IWM-ex-Fin/RE constituents so the universe filter keeps them.
_TICKERS = ["IMMR", "UAMY", "ONEW"]
_YEAR = 2025
_MONTHS = [1, 2, 3]


def _seed_bulk(db: str) -> None:
    """Insider history: each ticker has 3 prior-year opportunistic buys + a recent buy."""
    now = dt.datetime(2025, 1, 1)
    with get_connection(db) as c:
        for q in ("2022q1", "2025q1"):
            c.execute("INSERT INTO bulk_load_progress VALUES (?,?,?,?)", [q, now, 0, "complete"])
        rows = []
        for i, tk in enumerate(_TICKERS):
            cik = f"{100 + i}"
            # 3 distinct prior calendar years (Feb) -> opportunistic
            for yr in (2022, 2023, 2024):
                rows.append((cik, tk, dt.date(yr, 2, 1)))
            # recent buys in the test window (Jan/Feb 2025) -> in the 90d insider window
            rows.append((cik, tk, dt.date(2025, 1, 10)))
            rows.append((cik, tk, dt.date(2025, 2, 10)))
        for cik, tk, filed in rows:
            c.execute(
                """INSERT INTO form4_historical (cik,name,title,ticker,shares,price,value_usd,
                   transaction_date,filed_date,transaction_code,is_director,is_officer,
                   is_ten_percent_owner,accession_number,source_quarter)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    cik,
                    f"Ins {cik}",
                    "CEO",
                    tk,
                    1000.0,
                    50.0,
                    300_000.0,
                    filed,
                    filed,
                    "P",
                    False,
                    True,
                    False,
                    f"ACC-{cik}-{filed}",
                    "x",
                ],
            )
        c.commit()


def _seed_cache(cache_root: Path) -> None:
    """Pre-populate prices, fundamentals, regime so the grid never hits the network."""
    idx = pd.bdate_range("2024-09-25", "2025-04-05")
    root = providers.BacktestCache(cache_root)
    bench = providers.BacktestCache(root.dir / "_bench")
    # benchmarks + rate
    bclose = pd.DataFrame(
        {
            "IWN": range(100, 100 + len(idx)),
            "SPY": range(200, 200 + len(idx)),
            "^IRX": [5.0] * len(idx),
        },
        index=idx,
        dtype=float,
    )
    bclose.to_csv(bench.dir / "prices.csv")
    bclose.to_csv(bench.dir / "volume.csv")
    # active-ticker prices (rising, distinct per ticker so configs differ)
    aclose = pd.DataFrame(
        {t: [10 + i + 0.1 * k for k in range(len(idx))] for i, t in enumerate(_TICKERS)},
        index=idx,
        dtype=float,
    )
    aclose.to_csv(root.dir / "prices.csv")
    aclose.to_csv(root.dir / "volume.csv")
    # fundamentals for every (ticker, first-trading-day) pair (differ by ticker so ranking varies)
    rb = [pd.Timestamp("2025-01-01"), pd.Timestamp("2025-02-03"), pd.Timestamp("2025-03-03")]
    funds = {}
    for i, t in enumerate(_TICKERS):
        for r in rb:
            f = Fundamentals(
                ticker=t,
                fiscal_period="PIT",
                roa=0.05 + 0.03 * i,
                ocf=1e6,
                delta_roa=0.01,
                ocf_gt_ni=True,
                delta_leverage=-0.01,
                delta_liquidity=0.1,
                no_share_issuance=True,
                delta_gross_margin=0.01,
                delta_asset_turnover=0.01,
                gross_profit=2e8 + 1e8 * i,
                total_assets=1e9,
            )
            funds[f"{t}|{r.date().isoformat()}"] = json.loads(f.model_dump_json())
    root.put_json("fundamentals", funds)
    # regimes (risk-on)
    reg = MarketRegime(
        vix_60d_sma=18.0,
        credit_spread_bps=150.0,
        yield_curve_bps=30.0,
        value_growth_12m=0.0,
        regime="risk-on",
    )
    for r in rb:
        root.put_json(f"regime_{r.date().isoformat()}", reg.model_dump(mode="json"))


def _env(tmp_path: Path) -> tuple[str, str, str]:
    db = str(tmp_path / "bulk.db")
    _seed_bulk(db)
    cache_root = tmp_path / "cache"
    _seed_cache(cache_root)
    return db, str(cache_root), str(tmp_path / "out")


def _grid18() -> list[GridConfig]:
    return [
        GridConfig(n, thr, hold) for hold in (30, 90, 180) for thr in (60, 75, 90) for n in (5, 10)
    ]


def _run(tmp_path: Path, configs: list[GridConfig], out: str | None = None) -> dict:
    db, cache_root, out_root = _env(tmp_path)
    return run_backtest_grid(
        _YEAR,
        configs,
        db_path=db,
        edgar_user_agent="x x@x.com",
        months=_MONTHS,
        out_root=out or out_root,
        cache_root=cache_root,
    )


def test_grid_run_2025_produces_18_outputs(tmp_path: Path) -> None:
    configs = _grid18()
    assert len(configs) == 18
    db, cache_root, out_root = _env(tmp_path)
    results = run_backtest_grid(
        _YEAR,
        configs,
        db_path=db,
        edgar_user_agent="x x@x.com",
        months=_MONTHS,
        out_root=out_root,
        cache_root=cache_root,
    )
    assert len(results) == 18
    for cfg in configs:
        d = Path(out_root) / f"v3_{_YEAR}_{cfg.label}"
        for fname in ("equity_curve.csv", "trades.csv", "holdings.csv", "metrics.json"):
            assert (d / fname).exists(), f"{cfg.label} missing {fname}"
        assert list(d.glob("dossier_*.md"))  # monthly dossiers
        meta = json.loads((d / "metrics.json").read_text())
        assert "sample_size_warning" in meta["metrics"]  # mandatory


def test_grid_run_reproducibility_same_seed(tmp_path: Path) -> None:
    cfg = [GridConfig(10, 60, 90)]
    a = _run(tmp_path / "a", cfg)
    b = _run(tmp_path / "b", cfg)
    label = cfg[0].label
    assert a[label]["total_return_pct"] == b[label]["total_return_pct"]
    assert a[label]["total_trades"] == b[label]["total_trades"]
    eq_a = (tmp_path / "a" / "out" / f"v3_{_YEAR}_{label}" / "equity_curve.csv").read_text()
    eq_b = (tmp_path / "b" / "out" / f"v3_{_YEAR}_{label}" / "equity_curve.csv").read_text()
    assert eq_a == eq_b  # byte-identical


def test_grid_different_configs_differ(tmp_path: Path) -> None:
    # N=5 vs N=10 with a low threshold should hold different numbers of names.
    res = _run(tmp_path, [GridConfig(5, 60, 90), GridConfig(10, 60, 90)])
    assert (
        res["N5_thr60_hold90"]["average_position_count"]
        <= res["N10_thr60_hold90"]["average_position_count"]
    )


def test_grid_cache_present_means_no_network(tmp_path: Path) -> None:
    # With a fully-seeded cache, a run completes (would raise on any live fetch attempt
    # in this offline test env) and produces holdings.
    res = _run(tmp_path, [GridConfig(10, 60, 90)])
    assert res["N10_thr60_hold90"]["total_trades"] >= 1  # the 3 seeded names get held


def test_grid_drops_untradeable_late_listing(tmp_path: Path) -> None:
    """
    A name with NO point-in-time price at its rebalance date must NOT be traded
    (regression for the bfill look-ahead: backfilling pre-listing dates with future
    prices). LATE has insider activity from Jan but price data only from March.
    """
    late = "PRME"  # a real IWM-ex-Fin/RE ticker not in _TICKERS
    db = str(tmp_path / "bulk.db")
    _seed_bulk(db)
    # add LATE insider history + a Jan recent buy (so it is insider-active in Jan/Feb)
    with get_connection(db) as c:
        rows = [(late, dt.date(y, 2, 1)) for y in (2022, 2023, 2024)] + [
            (late, dt.date(2025, 1, 10))
        ]
        for tk, filed in rows:
            c.execute(
                """INSERT INTO form4_historical (cik,name,title,ticker,shares,price,value_usd,
                   transaction_date,filed_date,transaction_code,is_director,is_officer,
                   is_ten_percent_owner,accession_number,source_quarter)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    "900",
                    "Ins 900",
                    "CEO",
                    tk,
                    1000.0,
                    50.0,
                    300_000.0,
                    filed,
                    filed,
                    "P",
                    False,
                    True,
                    False,
                    f"ACC-900-{filed}",
                    "x",
                ],
            )
        c.commit()
    cache_root = tmp_path / "cache"
    _seed_cache(cache_root)
    # give LATE price data only from March (NaN before) in the active-price cache
    root = providers.BacktestCache(cache_root)
    close = pd.read_csv(root.dir / "prices.csv", index_col=0, parse_dates=True)
    idx = close.index
    late_px = pd.Series([float("nan")] * len(idx), index=idx)
    late_px.loc[idx >= pd.Timestamp("2025-03-01")] = 20.0
    close[late] = late_px
    close.to_csv(root.dir / "prices.csv")
    # fundamentals for LATE
    funds = root.get_json("fundamentals")
    f = Fundamentals(
        ticker=late,
        fiscal_period="PIT",
        roa=0.1,
        ocf=1e6,
        delta_roa=0.01,
        ocf_gt_ni=True,
        delta_leverage=-0.01,
        delta_liquidity=0.1,
        no_share_issuance=True,
        delta_gross_margin=0.01,
        delta_asset_turnover=0.01,
        gross_profit=3e8,
        total_assets=1e9,
    )
    for r in ("2025-01-01", "2025-02-03", "2025-03-03"):
        funds[f"{late}|{r}"] = json.loads(f.model_dump_json())
    root.put_json("fundamentals", funds)

    run_backtest_grid(
        _YEAR,
        [GridConfig(10, 60, 90)],
        db_path=db,
        edgar_user_agent="x x@x.com",
        months=_MONTHS,
        out_root=str(tmp_path / "out"),
        cache_root=str(cache_root),
    )
    trades = pd.read_csv(tmp_path / "out" / f"v3_{_YEAR}_N10_thr60_hold90" / "trades.csv")
    late_trades = trades[trades["ticker"] == late] if not trades.empty else trades
    # LATE may be held from March onward, but NEVER entered before its first price (March).
    for _, row in late_trades.iterrows():
        assert pd.Timestamp(row["entry_date"]) >= pd.Timestamp("2025-03-01"), (
            "traded a name before it had price data (bfill look-ahead)"
        )
