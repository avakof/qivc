#!/usr/bin/env python
"""
Task 14 Phase C — PIT 2-year primary test (2022 + 2025), H10 / H13 / H14.

    uv run python scripts/run_pit_2year_test.py

The pre-registered regime test, now survivor-bias-corrected: same 3 frozen
hypotheses and the same full 18-config structural grid as Task 12, but the
universe is point-in-time IWM N-PORT membership (SecPitProvider) and held names
that delist mid-hold are marked down by the conservative B3 rules — so the
previously-invisible losers finally show up.

Headline metric = pooled Score-Return R^2 per hypothesis PER YEAR. Pre-registered
criterion: does H14 (and H13) beat H10 baseline R^2 in BOTH 2022 AND 2025?
2022 (trend/down) is decisive; a 2025-only win = "regime artifact, not edge."

Data is assembled per year into data/backtests/_cache_pit_<year> (prices via
yfinance, fundamentals via EDGAR, regime cached) and is reproducible. CMP lookback
for 2022 uses 2019-2021 history already in form4_historical. ALL IN-SAMPLE,
bias-corrected; DO NOT DEPLOY. Writes BACKTEST_PIT_2YEAR_TEST.md.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from qivc.backtest import engine, providers
from qivc.backtest.composite_scorer import score_universe
from qivc.backtest.delisting_returns import DELISTING_MULTIPLIER
from qivc.backtest.pit_regime import regime_as_of
from qivc.backtest.pit_universe import SecPitProvider
from qivc.backtest.portfolio_constructor import GridConfig, Position, construct_portfolio
from qivc.backtest.universe import iwm_sector_map
from qivc.backtest.v3_factors import assemble_factors, opportunistic_insider_raw
from qivc.config import Settings
from qivc.data.edgar_client import EdgarClient
from qivc.schemas import MarketRegime

YEARS = [2022, 2025]
DOC = Path("BACKTEST_PIT_2YEAR_TEST.md")
_PRICE_WARMUP = dt.timedelta(days=95)
_GAMMA = 0.5772156649


def _w(i: float, q: float, v: float, m: float, t: float) -> dict[str, float]:
    return {"insider": i, "quality": q, "valuation": v, "momentum": m, "technical": t}


HYPOTHESES: dict[str, dict[str, float]] = {
    "H10": _w(0.40, 0.30, 0.20, 0.10, 0.00),
    "H13": _w(0.40, 0.00, 0.20, 0.10, 0.30),
    "H14": _w(0.50, 0.00, 0.10, 0.10, 0.30),
}
HYP_DESC = {"H10": "baseline (control)", "H13": "technical replaces quality",
            "H14": "insider+tech dominant (full Baldwin)"}


def grid18() -> list[GridConfig]:
    return [GridConfig(n, thr, hold)
            for hold in (30, 90, 180) for thr in (60, 75, 90) for n in (5, 10)]


def _phinv(p: float) -> float:
    a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
         138.357751867269, -30.66479806614716, 2.506628277459239]
    b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
         66.80131188771972, -13.28068155288572]
    c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416]
    pl = 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p <= 1 - pl:
        q = p - 0.5
        r = q * q
        return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)


def deflated_sharpe(eq: pd.Series, sr_ann_all: list[float]) -> float:
    n = len(sr_ann_all)
    if n < 2:
        return 0.0
    var_sr = float(np.var(sr_ann_all))
    sr0_ann = math.sqrt(var_sr) * (
        (1 - _GAMMA) * _phinv(1 - 1 / n) + _GAMMA * _phinv(1 - 1 / (n * math.e)))
    mr = eq.resample("ME").last().pct_change().dropna()
    if len(mr) < 3 or mr.std() == 0:
        return 0.0
    sr_m = float(mr.mean() / mr.std())
    sr0_m = sr0_ann / math.sqrt(12)
    sk = float(((mr - mr.mean()) ** 3).mean() / mr.std() ** 3)
    ku = float(((mr - mr.mean()) ** 4).mean() / mr.std() ** 4)
    den = math.sqrt(max(1e-9, 1 - sk * sr_m + ((ku - 1) / 4) * sr_m**2))
    return 0.5 * (1 + math.erf((sr_m - sr0_m) * math.sqrt(len(mr) - 1) / den / math.sqrt(2)))


def _r2(pts: list[tuple[float, float]]) -> float:
    if len(pts) < 3:
        return float("nan")
    arr = np.array(pts)
    if np.std(arr[:, 0]) == 0:
        return 0.0
    return float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1]) ** 2


def _top3(contribs: list[float]) -> float:
    tot = sum(contribs)
    return (sum(sorted(contribs, reverse=True)[:3]) / tot * 100) if tot else float("nan")


# Neutral (risk-on, 100% equity) fallback used ONLY when FRED is unreachable.
# The regime overlay merely scales position sizing (risk-off -> 60% equity); it does
# NOT change which names are held or their per-trade returns, so the headline
# Score-Return R^2 is INDEPENDENT of it. Fallback dates are counted + reported.
_NEUTRAL_REGIME = MarketRegime(
    vix_60d_sma=20.0, credit_spread_bps=150.0, yield_curve_bps=50.0,
    value_growth_12m=0.0, regime="risk-on",
)
_REGIME_FALLBACKS: dict[int, int] = {}
_FRED_DOWN = False  # set on first FRED failure -> skip FRED for all later dates


def _regime_cached(cache: providers.BacktestCache, d: dt.date, hc: httpx.Client) -> MarketRegime:
    global _FRED_DOWN
    rec = cache.get_json(f"regime_{d.isoformat()}")
    if rec is not None:
        return MarketRegime(**rec)
    if not _FRED_DOWN:
        try:
            reg = regime_as_of(d, client=hc)
            cache.put_json(f"regime_{d.isoformat()}", reg.model_dump(mode="json"))
            return reg
        except httpx.HTTPError:
            _FRED_DOWN = True  # FRED unreachable; neutral for all remaining dates
    _REGIME_FALLBACKS[d.year] = _REGIME_FALLBACKS.get(d.year, 0) + 1
    return _NEUTRAL_REGIME


def prepare_year(year: int, s: Settings, sec: SecPitProvider) -> dict:
    """Assemble PIT factor data + delisting-adjusted prices for one year."""
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    cache = providers.BacktestCache(Path(f"data/backtests/_cache_pit_{year}"))
    sector_map = iwm_sector_map()

    # Benchmarks + calendar.
    bench = providers.BacktestCache(cache.dir / "_bench")
    providers.build_price_cache(
        bench, ["IWN", "SPY", "^IRX"], start - _PRICE_WARMUP, end + dt.timedelta(days=3))
    bench_close, _ = providers.load_prices(bench)
    calendar = pd.DatetimeIndex(bench_close.index)
    rebalance = engine.first_trading_days(calendar, start, end)

    # PIT universe per rebalance (no look-ahead) + insider-active set.
    universe_by_rb = {r.date(): sec.get_constituents(r.date()) for r in rebalance}
    insider_by_rb = {
        r.date(): opportunistic_insider_raw(s.db_path, universe_by_rb[r.date()], r.date())
        for r in rebalance
    }
    active = sorted({t for d in insider_by_rb.values() for t in d})

    # Short timeout so a down FRED fails fast to the neutral fallback (R^2 is
    # regime-independent; only 2022 hits FRED since 2025 regimes are cached).
    with httpx.Client(timeout=8.0) as hc:
        regimes = {r.date(): _regime_cached(cache, r.date(), hc) for r in rebalance}

    if active:
        providers.build_price_cache(
            cache, active, start - _PRICE_WARMUP, end + dt.timedelta(days=3))
        client = EdgarClient(s.edgar_user_agent, rate_limit_rps=s.edgar_rate_limit_rps)
        ticker_dates = [(t, r.date()) for r in rebalance for t in insider_by_rb[r.date()]]
        providers.build_fundamentals_cache(cache, client, ticker_dates)
        close, _vol = providers.load_prices(cache)
    else:
        close = pd.DataFrame()
    fund_provider = providers.make_fundamentals_provider(cache)
    tech_provider = providers.make_technical_provider(close)

    # Conservative delisting overrides: for any priced name that delists during the
    # year, set price >= filed_date to last_valid x B3 multiplier (the loss finally
    # shows up). reason is 'unknown' in the data -> -50% (pre-registered default).
    delisted_marks: dict[str, tuple[dt.date, str]] = {}
    for t in list(close.columns):
        ev = sec.get_delisting_event(t)
        if ev is None or not (start <= ev.filed_date <= end):
            continue
        ser = close[t]
        prior = ser.loc[ser.index < pd.Timestamp(ev.filed_date)].dropna()
        if prior.empty:
            continue
        mult = DELISTING_MULTIPLIER.get(ev.reason, DELISTING_MULTIPLIER["unknown"])
        mark = float(prior.iloc[-1]) * mult
        close.loc[close.index >= pd.Timestamp(ev.filed_date), t] = mark
        delisted_marks[t] = (ev.filed_date, ev.reason)

    # Assemble factors once per month (universe is the PIT set for that month).
    factors_by_rb = {
        r.date(): assemble_factors(
            r.date(), db_path=s.db_path, universe=universe_by_rb[r.date()],
            sector_map=sector_map, fundamentals_provider=fund_provider,
            technical_provider=tech_provider)
        for r in rebalance
    }

    period = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    irx = bench_close["^IRX"] if "^IRX" in bench_close else pd.Series(0.0, index=calendar)
    return {
        "rebalance": rebalance, "period": period, "close": close,
        "irx": irx, "iwn": bench_close["IWN"].reindex(period).ffill(),
        "spy": bench_close["SPY"].reindex(period).ffill(),
        "factors_by_rb": factors_by_rb, "regimes": regimes,
        "delisted_marks": delisted_marks, "n_active": len(active),
        "universe_sizes": {d: len(u) for d, u in universe_by_rb.items()},
    }


def run_config(cfg: GridConfig, scored_by_rb: dict, prep: dict) -> dict:
    close, period, rebalance = prep["close"], prep["period"], prep["rebalance"]
    regimes, irx = prep["regimes"], prep["irx"]
    prev: list[Position] = []
    holdings_by_date: dict[pd.Timestamp, list[tuple[str, float]]] = {}
    for r in rebalance:
        d = r.date()
        positions = construct_portfolio(scored_by_rb[d], prev, d, cfg, regimes[d])
        prev = positions
        holdings_by_date[r] = [(p.ticker, p.weight) for p in positions]

    held = sorted({t for hs in holdings_by_date.values() for t, _ in hs})
    price = pd.DataFrame(index=period)
    for t in held:
        if t in close.columns:
            price[t] = close[t].reindex(period).ffill()
    price[engine._CASH] = engine.build_cash_series(period, irx)
    price = price.dropna(axis=1, how="all")
    rb_in = [r for r in rebalance if r in period]
    tradeable = {
        r: [(t, w) for t, w in holdings_by_date.get(r, [])
            if t in price.columns and not pd.isna(price[t].loc[r])]
        for r in rb_in
    }
    weights = engine.build_target_weights(price, rb_in, tradeable)
    equity = engine.simulate(price, weights, init_cash=1_000_000.0,
                             slippage_bps=5.0, commission_bps=1.0)
    trades = engine.extract_trades(price, weights)
    snaps = engine.holdings_snapshots(weights, rb_in)
    monthly = equity.resample("ME").last().pct_change().dropna()
    metrics = engine.compute_metrics(equity, monthly, trades, prep["iwn"], prep["spy"], {}, snaps)

    entry_w = {(r, t): w for r, lst in tradeable.items() for t, w in lst}
    comp_at = {(d, x.ticker): x.composite for d, lst in scored_by_rb.items() for x in lst}
    pts, contribs, held_delisted = [], [], set()
    for _, x in trades[trades["return_pct"].notna()].iterrows():
        ed = pd.Timestamp(x["entry_date"])
        ret = float(x["return_pct"])
        if (ed.date(), x["ticker"]) in comp_at:
            pts.append((comp_at[(ed.date(), x["ticker"])], ret * 100))
        contribs.append(float(entry_w.get((ed, x["ticker"]), 0.0)) * ret)
        if x["ticker"] in prep["delisted_marks"]:
            held_delisted.add(x["ticker"])
    return {"metrics": metrics, "equity": equity, "pts": pts, "contribs": contribs,
            "held_delisted": held_delisted}


def main() -> None:
    s = Settings()
    sec = SecPitProvider(s.db_path)
    configs = grid18()
    results: dict[int, dict] = {}
    all_sharpes: list[float] = []
    for year in YEARS:
        print(f"=== preparing {year} ===")
        prep = prepare_year(year, s, sec)
        print(f"  {year}: {prep['n_active']} insider-active names, "
              f"{len(prep['delisted_marks'])} delisting marks in universe")
        per_hyp = {}
        for hyp, weights in HYPOTHESES.items():
            scored_by_rb = {d: score_universe(prep["factors_by_rb"][d], weights=weights)
                            for d in prep["factors_by_rb"]}
            per_cfg = {cfg.label: run_config(cfg, scored_by_rb, prep) for cfg in configs}
            for r in per_cfg.values():
                all_sharpes.append(r["metrics"]["sharpe"])
            per_hyp[hyp] = per_cfg
            print(f"  {year} {hyp}: 18 configs done")
        results[year] = {"per_hyp": per_hyp, "prep": prep}

    _write_report(results, all_sharpes)
    print(f"\nwrote {DOC}")


def _agg(per_cfg: dict) -> dict:
    labels = list(per_cfg)
    pooled = [p for lb in labels for p in per_cfg[lb]["pts"]]
    rets = [per_cfg[lb]["metrics"]["total_return_pct"] for lb in labels]
    shs = [per_cfg[lb]["metrics"]["sharpe"] for lb in labels]
    trd = [per_cfg[lb]["metrics"]["total_trades"] for lb in labels]
    top3 = [_top3(per_cfg[lb]["contribs"]) for lb in labels]
    wins = [p[1] for p in pooled]
    held_del = set().union(*(per_cfg[lb]["held_delisted"] for lb in labels)) if labels else set()
    best_lb = max(labels, key=lambda lb: per_cfg[lb]["metrics"]["sharpe"])
    return {
        "r2": _r2(pooled), "n": len(pooled),
        "med_return": float(np.median(rets)), "med_sharpe": float(np.median(shs)),
        "med_trades": float(np.median(trd)), "med_top3": float(np.nanmedian(top3)),
        "win": (sum(1 for w in wins if w > 0) / len(wins) * 100) if wins else float("nan"),
        "held_delisted": sorted(held_del),
        "best_eq": per_cfg[best_lb]["equity"], "best_sharpe": per_cfg[best_lb]["metrics"]["sharpe"],
    }


def _write_report(results: dict, all_sharpes: list[float]) -> None:
    agg = {yr: {h: _agg(results[yr]["per_hyp"][h]) for h in HYPOTHESES} for yr in YEARS}
    L: list[str] = []
    L.append("# Task 14 Phase C — PIT 2-Year Regime Test (2022 + 2025)\n")
    L.append(
        "> ⚠️ **SURVIVOR-BIAS-CORRECTED but still IN-SAMPLE. DO NOT DEPLOY.** Point-in-time "
        "IWM N-PORT membership + conservative B3 delisting marks, full 18-config grid "
        "(matching Task 12), 3 frozen hypotheses. Headline = pooled Score-Return R² per "
        "year. A pass means 'worth forward validation,' not 'deploy.' **2022 is the "
        "decisive (trend/down) regime; a 2025-only win = regime artifact.**\n")

    L.append("## Sensitivity bound (2022 membership coverage)\n")
    L.append(
        "- 2022 raw membership coverage: **93.9%** (1,874/1,996 N-PORT holdings mapped).\n"
        "- Residual unmapped: **122** names (de-SPACs / recent IPOs: Cano Health, Nikola, "
        "Sovos, Audacy, Hyzon, …).\n"
        "- **Residual names with 2022 insider buying: 0** (confirmed issuer-side via CIK + "
        "owner-name cross-check). Every unmapped name is outside the insider-active scored "
        "subset, so **the regime test is unaffected by the residual.**\n"
        "- Bias-correction magnitude: the 2022 PIT universe **restores 567** wrongly-excluded "
        "names and **drops 659** wrongly-included vs the current snapshot (~2/3 of the "
        "universe differs).\n")

    L.append("## Result — pooled Score-Return R² per year (the headline)\n")
    L.append("| Hyp | Weights | 2022 R² | 2025 R² | 2022 ret(med) | 2025 ret(med) "
             "| 2022 Sharpe(med) | 2025 Sharpe(med) |")
    L.append("|" + "---|" * 8)
    for h in HYPOTHESES:
        w = "/".join(str(int(HYPOTHESES[h][k] * 100))
                     for k in ("insider", "quality", "valuation", "momentum", "technical"))
        a22, a25 = agg[2022][h], agg[2025][h]
        L.append(f"| {h} | {w} | **{a22['r2']:.4f}** | **{a25['r2']:.4f}** "
                 f"| {a22['med_return']:.1f}% | {a25['med_return']:.1f}% "
                 f"| {a22['med_sharpe']:.2f} | {a25['med_sharpe']:.2f} |")

    L.append("\n## Per-year detail (win% and top-3 concentration pooled/median over 18 configs)\n")
    for yr in YEARS:
        L.append(f"\n**{yr}**\n")
        L.append("| Hyp | Trades(med) | Return(med) | Sharpe(med) | Win% | Top3% | R² "
                 "| DSR(N=3) | Held names that delisted |")
        L.append("|" + "---|" * 9)
        sr3 = [agg[yr][h]["best_sharpe"] for h in HYPOTHESES]
        for h in HYPOTHESES:
            a = agg[yr][h]
            dsr = deflated_sharpe(a["best_eq"], sr3)
            hd = ", ".join(a["held_delisted"]) or "none"
            L.append(f"| {h} | {a['med_trades']:.0f} | {a['med_return']:.1f}% "
                     f"| {a['med_sharpe']:.2f} | {a['win']:.0f} | {a['med_top3']:.0f} "
                     f"| {a['r2']:.4f} | {dsr:.3f} | {hd} |")

    # Verdict
    base22, base25 = agg[2022]["H10"]["r2"], agg[2025]["H10"]["r2"]
    def beats(h: str) -> tuple[bool, bool]:
        return agg[2022][h]["r2"] > base22, agg[2025][h]["r2"] > base25
    L.append("\n## Verdict vs the pre-registered criterion\n")
    L.append(f"Baseline H10 pooled R²: 2022 = {base22:.4f}, 2025 = {base25:.4f}.\n")
    verdict_pass = True
    for h in ("H13", "H14"):
        b22, b25 = beats(h)
        both = b22 and b25
        verdict_pass = verdict_pass and both
        L.append(f"- **{h}** ({HYP_DESC[h]}): beats H10 in 2022 = **{b22}** "
                 f"(R² {agg[2022][h]['r2']:.4f} vs {base22:.4f}); "
                 f"in 2025 = **{b25}** (R² {agg[2025][h]['r2']:.4f} vs {base25:.4f}) "
                 f"-> BOTH = **{both}**.\n")
    L.append(
        f"\n### {'PASS' if verdict_pass else 'FAIL'} — "
        + ("H13 and H14 beat baseline R² in BOTH years. The technical-oversold signal "
           "is NOT a 2025 mean-reversion artifact on this survivor-bias-free test — it "
           "earns the 4-year confirmation (Phase D). Still IN-SAMPLE; DO NOT DEPLOY."
           if verdict_pass else
           "At least one of H13/H14 does NOT beat baseline R² in BOTH years. Per the "
           "pre-registered criterion, the in-sample Task-12 result was substantially a "
           "**regime artifact** once survivor bias is removed. STOP — do not run Phase D. "
           "DO NOT DEPLOY.") + "\n")
    if not beats("H14")[0] or not beats("H13")[0]:
        L.append("\n**2022 (decisive) specifically:** at least one technical hypothesis "
                 "FAILED to beat baseline in the trend/down regime -> regime artifact.\n")

    L.append("\n## Deflated Sharpe note\n")
    L.append("DSR accounts for the 3 hypotheses (N=3) per year; raw best Sharpes feed it. "
             "At T=12 months and 3 trials these are weak either way and **do not tip the "
             "decision** — R² consistency across the two regimes is the real read.\n")

    if _REGIME_FALLBACKS:
        L.append("\n## Methodology caveat — regime overlay (FRED unavailable)\n")
        L.append(
            "FRED (credit-spread / yield-curve inputs) was unreachable during this run, so "
            f"{_REGIME_FALLBACKS} rebalance month(s) used a **neutral risk-on regime** "
            "(100% equity) instead of the live classification. 2025 used the real "
            "FRED-derived regimes cached in Phase 4A. **This does not affect the verdict:** "
            "the regime overlay only scales position sizing (risk-off -> 60% equity); it "
            "does not change which names are held or their per-trade returns, so the "
            "headline Score-Return R² is identical with or without it. Only the secondary "
            "return/Sharpe columns for fallback months omit risk-off de-risking (a uniform "
            "effect across all three hypotheses, so the comparison still holds).\n")
    DOC.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
