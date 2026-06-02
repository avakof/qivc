#!/usr/bin/env python
"""
Task 11 — Phase 4A deep-dive analysis + visualizations.

Reads the 18 existing 2025 grid runs in data/backtests/v3_2025_*/ and the warm
factor cache (data/backtests/_cache_v3_2025) — NO new market fetches. Re-scores
the eligible universe per month from cached data + the local bulk store to get
score distributions and per-trade entry composites. Produces 9 PNGs in
data/backtests/analysis_4A/ and writes BACKTEST_ANALYSIS_4A.md.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from qivc.backtest import providers
from qivc.backtest.composite_scorer import score_universe
from qivc.backtest.universe import iwm_sector_map, iwm_tickers
from qivc.backtest.v3_factors import assemble_factors
from qivc.config import Settings

OUT = Path("data/backtests/analysis_4A")
OUT.mkdir(parents=True, exist_ok=True)
RUN_GLOB = "data/backtests/v3_2025_*"
EXCLUDE = {"Financials", "Real Estate"}
_GAMMA = 0.5772156649


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------
def load_runs() -> dict[str, dict]:
    runs = {}
    for d in sorted(glob.glob(RUN_GLOB)):
        p = Path(d)
        if not (p / "metrics.json").exists() or "analysis" in p.name:
            continue
        label = p.name.replace("v3_2025_", "")
        m = json.loads((p / "metrics.json").read_text())["metrics"]
        trades = pd.read_csv(p / "trades.csv") if (p / "trades.csv").exists() else pd.DataFrame()
        holds = pd.read_csv(p / "holdings.csv", index_col=0, parse_dates=True)
        eq = pd.read_csv(p / "equity_curve.csv", index_col=0, parse_dates=True)["equity"]
        runs[label] = {"metrics": m, "trades": trades, "holdings": holds, "equity": eq, "dir": p}
    return runs


def parse_cfg(label: str) -> tuple[int, int, int]:
    # N{n}_thr{thr}_hold{hold}
    n = int(label.split("_")[0][1:])
    thr = int(label.split("thr")[1].split("_")[0])
    hold = int(label.split("hold")[1])
    return n, thr, hold


def rescore_by_month(cache: providers.BacktestCache) -> dict[dt.date, list]:
    """Re-assemble factors + score per rebalance month from cached data (no fetch)."""
    s = Settings()
    sector_map = iwm_sector_map()
    universe = {t for t in iwm_tickers() if sector_map.get(t) not in EXCLUDE}
    fp = providers.make_fundamentals_provider(cache)
    # rebalance dates = holdings.csv snapshot index of any run
    any_run = next(iter(load_runs().values()))
    dates = [d.date() for d in any_run["holdings"].index]
    out = {}
    for d in dates:
        factors = assemble_factors(
            d,
            db_path=s.db_path,
            universe=universe,
            sector_map=sector_map,
            fundamentals_provider=fp,
        )
        out[d] = score_universe(factors)
    return out


# --------------------------------------------------------------------------
# DSR (deflated Sharpe) over the 18 trials
# --------------------------------------------------------------------------
def _phinv(p: float) -> float:
    a = [
        -39.69683028665376,
        220.9460984245205,
        -275.9285104469687,
        138.357751867269,
        -30.66479806614716,
        2.506628277459239,
    ]
    b = [
        -54.47609879822406,
        161.5858368580409,
        -155.6989798598866,
        66.80131188771972,
        -13.28068155288572,
    ]
    c = [
        -0.007784894002430293,
        -0.3223964580411365,
        -2.400758277161838,
        -2.549732539343734,
        4.374664141464968,
        2.938163982698783,
    ]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416]
    pl = 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p <= 1 - pl:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        )
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
    )


def deflated_sharpe(eq: pd.Series, sr_ann_all: list[float]) -> float:
    n = len(sr_ann_all)
    var_sr = float(np.var(sr_ann_all)) if n > 1 else 0.0
    sr0_ann = math.sqrt(var_sr) * (
        (1 - _GAMMA) * _phinv(1 - 1 / n) + _GAMMA * _phinv(1 - 1 / (n * math.e))
    )
    mr = eq.resample("ME").last().pct_change().dropna()
    if len(mr) < 3 or mr.std() == 0:
        return 0.0
    sr_m = float(mr.mean() / mr.std())
    sr0_m = sr0_ann / math.sqrt(12)
    sk = float(((mr - mr.mean()) ** 3).mean() / mr.std() ** 3)
    ku = float(((mr - mr.mean()) ** 4).mean() / mr.std() ** 4)
    den = math.sqrt(max(1e-9, 1 - sk * sr_m + ((ku - 1) / 4) * sr_m**2))
    return 0.5 * (1 + math.erf((sr_m - sr0_m) * math.sqrt(len(mr) - 1) / den / math.sqrt(2)))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> None:
    runs = load_runs()
    labels = sorted(runs, key=parse_cfg)
    cache = providers.BacktestCache(Path("data/backtests/_cache_v3_2025"))
    close, _ = providers.load_prices(cache)
    bench, _ = providers.load_prices(providers.BacktestCache(cache.dir / "_bench"))
    scored = rescore_by_month(cache)
    md: list[str] = []

    # full-year 2025 return per name (cached prices)
    p25 = close.loc[(close.index >= "2025-01-02") & (close.index <= "2025-12-31")]
    name_ret = {
        t: float(p25[t].dropna().iloc[-1] / p25[t].dropna().iloc[0] - 1) * 100
        for t in p25.columns
        if p25[t].dropna().shape[0] > 1
    }

    sr_all = [runs[lb]["metrics"]["sharpe"] for lb in labels]

    stats: dict[str, float | str | list] = {}
    md.append(_analysis1(scored))
    md.append(_analysis2(runs, labels, sr_all))
    md.append(_analysis3(runs, labels))
    md.append(_analysis4(runs, labels, name_ret, sr_all, stats))
    md.append(_analysis5(runs, labels, scored, stats))
    md.append(_analysis6(runs, labels, bench))
    md.append(_analysis7(runs))
    md.append(_interpretation(runs, labels, stats))

    header = (
        "# QIVC v3.0 — Phase 4A Deep-Dive Analysis (Task 11)\n\n"
        "> ⚠️ Analysis of 18 backtest runs over **one year (2025)**, 9-44 trades each "
        "— **one observation**, below any significance floor. The Phase 4A returns are "
        "**survivor-bias-suspect** (BACKTEST_REVIEW_V3_GRID.md). This document dissects "
        "*what the system did*; it does not establish edge.\n\n"
        "Plots in `data/backtests/analysis_4A/`. Analysis-only; no new market data, no "
        "new runs.\n"
    )
    Path("BACKTEST_ANALYSIS_4A.md").write_text(header + "\n".join(md))
    print("wrote BACKTEST_ANALYSIS_4A.md +", len(list(OUT.glob("*.png"))), "plots")


def _img(name: str) -> str:
    return f"\n![{name}](data/backtests/analysis_4A/{name})\n"


def _analysis1(scored: dict[dt.date, list]) -> str:
    months = sorted(scored)
    data = [[s.composite for s in scored[m]] for m in months]
    fig, ax = plt.subplots(figsize=(13, 6))
    ax.violinplot([d if d else [0] for d in data], showmedians=True)
    for i, d in enumerate(data, 1):
        if not d:
            continue
        for q, c in ((60, "tab:orange"), (75, "tab:green"), (90, "tab:red")):
            ax.hlines(np.percentile(d, q), i - 0.3, i + 0.3, color=c, lw=1, alpha=0.7)
    ax.set_xticks(range(1, len(months) + 1))
    ax.set_xticklabels([m.strftime("%b") for m in months])
    ax.set_ylabel("composite score")
    ax.set_title(
        "Analysis 1 — Monthly composite-score distribution (eligible universe)\n"
        "orange/green/red lines = 60/75/90th pct thresholds"
    )
    fig.tight_layout()
    fig.savefig(OUT / "monthly_score_distributions.png", dpi=110)
    plt.close(fig)

    rows = ["| Month | N | median | IQR | top-5 composites |", "|---|---|---|---|---|"]
    for m in months:
        d = sorted((s.composite for s in scored[m]), reverse=True)
        if not d:
            rows.append(f"| {m:%Y-%m} | 0 | — | — | — |")
            continue
        iqr = np.percentile(d, 75) - np.percentile(d, 25)
        top5 = ", ".join(f"{x:.3f}" for x in d[:5])
        rows.append(f"| {m:%Y-%m} | {len(d)} | {np.median(d):.3f} | {iqr:.3f} | {top5} |")
    return (
        "## Analysis 1 — Score distribution\n"
        + _img("monthly_score_distributions.png")
        + "\n"
        + "\n".join(rows)
        + "\n"
    )


def _analysis2(runs: dict, labels: list[str], sr_all: list[float]) -> str:
    rows = [
        "| Config | Return% | Sharpe | DSR | Trades | AvgHold | MaxDD% |",
        "|---|---|---|---|---|---|---|",
    ]
    xs, ys, zs, sizes = [], [], [], []
    for lb in labels:
        m = runs[lb]["metrics"]
        n, thr, hold = parse_cfg(lb)
        dsr = deflated_sharpe(runs[lb]["equity"], sr_all)
        avg_hold = m.get("avg_hold_days") or 0
        rows.append(
            f"| {lb} | {m['total_return_pct']:.2f} | {m['sharpe']:.2f} | {dsr:.2f} | "
            f"{m['total_trades']} | {avg_hold:.0f} | {m['max_drawdown_pct']:.1f} |"
        )
        xs.append(hold)
        ys.append(thr)
        zs.append(m["total_return_pct"])
        sizes.append(120 if n == 10 else 40)
    # 3D scatter
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")
    sc = ax.scatter(xs, ys, zs, s=sizes, c=zs, cmap="viridis")
    ax.set_xlabel("holding days")
    ax.set_ylabel("threshold pct")
    ax.set_zlabel("return %")
    ax.set_title("Analysis 2 — config sensitivity (marker size = N: large=10)")
    fig.colorbar(sc, label="return %")
    fig.savefig(OUT / "config_sensitivity_3d.png", dpi=110)
    plt.close(fig)
    # heatmaps
    holds, thrs = [30, 90, 180], [60, 75, 90]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, n in zip(axes, (5, 10), strict=True):
        grid = np.array(
            [
                [runs[f"N{n}_thr{t}_hold{h}"]["metrics"]["total_return_pct"] for h in holds]
                for t in thrs
            ]
        )
        im = ax.imshow(grid, cmap="RdYlGn", aspect="auto")
        ax.set_xticks(range(3))
        ax.set_xticklabels(holds)
        ax.set_yticks(range(3))
        ax.set_yticklabels(thrs)
        ax.set_xlabel("holding days")
        ax.set_ylabel("threshold pct")
        ax.set_title(f"N={n} return %")
        for i in range(3):
            for j in range(3):
                ax.text(j, i, f"{grid[i, j]:.0f}", ha="center", va="center")
        fig.colorbar(im, ax=ax)
    fig.suptitle("Analysis 2 — return heatmap (threshold x holding)")
    fig.tight_layout()
    fig.savefig(OUT / "config_sensitivity_heatmap.png", dpi=110)
    plt.close(fig)
    # trades bar
    fig, ax = plt.subplots(figsize=(13, 5))
    ax.bar(range(len(labels)), [runs[lb]["metrics"]["total_trades"] for lb in labels])
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90, fontsize=7)
    ax.set_ylabel("trades")
    ax.set_title("Analysis 2 — trades per config")
    fig.tight_layout()
    fig.savefig(OUT / "trades_per_config.png", dpi=110)
    plt.close(fig)
    return (
        "## Analysis 2 — Configuration sensitivity\n"
        + _img("config_sensitivity_3d.png")
        + _img("config_sensitivity_heatmap.png")
        + _img("trades_per_config.png")
        + "\n"
        + "\n".join(rows)
        + "\n"
    )


def _all_trades(runs: dict, labels: list[str]) -> pd.DataFrame:
    frames = []
    for lb in labels:
        t = runs[lb]["trades"].copy()
        if not t.empty:
            t["config"] = lb
            frames.append(t)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _analysis3(runs: dict, labels: list[str]) -> str:
    allt = _all_trades(runs, labels)
    closed = allt[allt["return_pct"].notna()].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(closed["return_pct"] * 100, bins=40, color="tab:blue", edgecolor="white")
    ax.axvline(0, color="k", lw=1)
    ax.set_xlabel("per-trade return %")
    ax.set_ylabel("count")
    ax.set_title(f"Analysis 3 — per-trade returns (n={len(closed)} closed trades, all configs)")
    fig.tight_layout()
    fig.savefig(OUT / "trade_return_histogram.png", dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(closed["hold_days"], bins=30, color="tab:purple", edgecolor="white")
    ax.set_xlabel("holding days")
    ax.set_ylabel("count")
    ax.set_title("Analysis 3 — holding-period distribution")
    fig.tight_layout()
    fig.savefig(OUT / "hold_time_histogram.png", dpi=110)
    plt.close(fig)

    win = closed.nlargest(10, "return_pct")
    los = closed.nsmallest(10, "return_pct")

    def _tbl(df: pd.DataFrame, title: str) -> str:
        r = [
            f"**{title}**",
            "",
            "| Ticker | Config | Hold | Entry | Exit | Return% |",
            "|---|---|---|---|---|---|",
        ]
        for _, x in df.iterrows():
            r.append(
                f"| {x.ticker} | {x.config} | {x.hold_days:.0f} | {x.entry_price} | "
                f"{x.exit_price} | {x.return_pct * 100:.1f} |"
            )
        return "\n".join(r)

    cnt = allt["ticker"].value_counts()
    cnt_rows = [
        "**Trade count by ticker (appearances across configs)**",
        "",
        "| Ticker | # config-trades |",
        "|---|---|",
    ]
    for tk, c in cnt.head(15).items():
        cnt_rows.append(f"| {tk} | {c} |")
    return (
        "## Analysis 3 — Trade-level distribution\n"
        + _img("trade_return_histogram.png")
        + _img("hold_time_histogram.png")
        + "\n"
        + _tbl(win, "Top 10 winning trades")
        + "\n\n"
        + _tbl(los, "Top 10 losing trades")
        + "\n\n"
        + "\n".join(cnt_rows)
        + "\n"
    )


def _trade_contributions(run: dict) -> pd.DataFrame:
    """Approx per-trade portfolio P&L contribution = entry_weight x return."""
    t = run["trades"]
    closed = t[t["return_pct"].notna()].copy()
    holds = run["holdings"]
    contribs = []
    for _, x in closed.iterrows():
        ed = pd.Timestamp(x["entry_date"])
        w = (
            holds.loc[ed, x["ticker"]]
            if (ed in holds.index and x["ticker"] in holds.columns)
            else 0.0
        )
        contribs.append(float(w) * float(x["return_pct"]))
    closed["contrib"] = contribs
    return closed


def _analysis4(
    runs: dict, labels: list[str], name_ret: dict, sr_all: list[float], stats: dict
) -> str:
    winners = sorted([t for t, r in name_ret.items() if r > 100], key=lambda t: -name_ret[t])
    # median config return with vs without winner trades
    base, exwin = [], []
    for lb in labels:
        c = _trade_contributions(runs[lb])
        base.append(runs[lb]["metrics"]["total_return_pct"])
        ex = c[~c["ticker"].isin(winners)]["contrib"].sum() * 100
        exwin.append(ex)
    med_base, med_ex = float(np.median(base)), float(np.median(exwin))
    # best config (highest raw Sharpe) top-3 trade P&L share
    best = max(labels, key=lambda lb: runs[lb]["metrics"]["sharpe"])
    cb = _trade_contributions(runs[best]).sort_values("contrib", ascending=False)
    tot = cb["contrib"].sum()
    top3_share = (cb["contrib"].head(3).sum() / tot * 100) if tot else 0.0
    stats.update(
        {
            "med_base": med_base,
            "med_ex": med_ex,
            "top3_share": top3_share,
            "best": best,
            "n_winners": len(winners),
        }
    )
    # attribution bars: best + median config
    med_cfg = labels[int(np.argsort(base)[len(base) // 2])]
    for lb, fname in (
        (best, "pnl_attribution_best_config.png"),
        (med_cfg, "pnl_attribution_median_config.png"),
    ):
        c = _trade_contributions(runs[lb]).sort_values("contrib", ascending=False)
        fig, ax = plt.subplots(figsize=(11, 5))
        colors = ["tab:green" if v > 0 else "tab:red" for v in c["contrib"]]
        ax.bar(range(len(c)), c["contrib"] * 100, color=colors)
        ax.set_xticks(range(len(c)))
        ax.set_xticklabels([f"{t}" for t in c["ticker"]], rotation=90, fontsize=7)
        ax.set_ylabel("contribution to total return (pp)")
        ax.set_title(f"Analysis 4 — P&L attribution: {lb}")
        fig.tight_layout()
        fig.savefig(OUT / fname, dpi=110)
        plt.close(fig)
    rows = [
        "- **Known winners (2025 full-year > +100%)**: "
        + ", ".join(f"{t} (+{name_ret[t]:.0f}%)" for t in winners),
        f"- **Median config return (all trades): {med_base:.1f}%**",
        f"- **Median config return EXCLUDING winner-name trades: {med_ex:.1f}%** "
        f"→ a **{med_base - med_ex:.1f} pp** drop "
        f"({(1 - med_ex / med_base) * 100:.0f}% of the median return "
        "came from those few names).",
        f"- **Best config by raw Sharpe**: `{best}` — top-3 trades by P&L = "
        f"**{top3_share:.0f}%** of its total return.",
    ]
    return (
        "## Analysis 4 — Survivor-bias quantification (most important)\n"
        + _img("pnl_attribution_best_config.png")
        + _img("pnl_attribution_median_config.png")
        + "\n"
        + "\n".join(rows)
        + "\n"
    )


def _analysis5(runs: dict, labels: list[str], scored: dict, stats: dict) -> str:
    comp = {}
    for m, lst in scored.items():
        for s in lst:
            comp[(m, s.ticker)] = s.composite
    pts = []  # (composite, return, hold)
    for lb in labels:
        c = runs[lb]["trades"]
        closed = c[c["return_pct"].notna()]
        for _, x in closed.iterrows():
            key = (pd.Timestamp(x["entry_date"]).date(), x["ticker"])
            if key in comp:
                pts.append((comp[key], x["return_pct"] * 100, x["hold_days"]))
    arr = np.array(pts) if pts else np.zeros((0, 3))
    fig, ax = plt.subplots(figsize=(9, 6))
    corr = r2 = float("nan")
    if len(arr) > 2:
        sc = ax.scatter(arr[:, 0], arr[:, 1], c=arr[:, 2], cmap="plasma", alpha=0.6)
        fig.colorbar(sc, label="hold days")
        b1, b0 = np.polyfit(arr[:, 0], arr[:, 1], 1)
        xs = np.linspace(arr[:, 0].min(), arr[:, 0].max(), 50)
        ax.plot(xs, b1 * xs + b0, "k--", lw=2)
        corr = float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1])
        r2 = corr**2
    stats.update({"corr": corr, "r2": r2, "n_trades": len(arr)})
    ax.axhline(0, color="gray", lw=0.5)
    ax.set_xlabel("composite score at entry")
    ax.set_ylabel("realized return %")
    ax.set_title(f"Analysis 5 — entry composite vs realized return (corr={corr:.2f}, R²={r2:.3f})")
    fig.tight_layout()
    fig.savefig(OUT / "score_vs_return_scatter.png", dpi=110)
    plt.close(fig)
    return (
        "## Analysis 5 — Score-to-return relationship\n"
        + _img("score_vs_return_scatter.png")
        + f"\n- Pearson correlation (entry composite vs realized return): **{corr:.3f}**; "
        f"**R² = {r2:.3f}** over {len(arr)} trades.\n"
    )


def _analysis6(runs: dict, labels: list[str], bench: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(12, 7))
    cmap = {30: "tab:blue", 90: "tab:orange", 180: "tab:green"}
    for lb in labels:
        eq = runs[lb]["equity"]
        _, _, hold = parse_cfg(lb)
        ax.plot(eq.index, eq / eq.iloc[0], color=cmap[hold], alpha=0.45, lw=1)
    period = runs[labels[0]]["equity"].index
    for sym, c, ls in (("IWN", "black", "--"), ("SPY", "dimgray", ":")):
        if sym in bench:
            s = bench[sym].reindex(period).ffill()
            ax.plot(period, s / s.iloc[0], color=c, ls=ls, lw=2, label=sym)
    ax.axhline(1.04, color="brown", ls="-.", lw=1, label="T-bill ~4%")
    for h, c in cmap.items():
        ax.plot([], [], color=c, label=f"hold {h}d")
    ax.legend()
    ax.set_ylabel("growth of $1")
    ax.set_title("Analysis 6 — all 18 equity curves vs benchmarks")
    fig.tight_layout()
    fig.savefig(OUT / "equity_curves_all_configs.png", dpi=110)
    plt.close(fig)
    return (
        "## Analysis 6 — Equity curves\n"
        + _img("equity_curves_all_configs.png")
        + "\n(Colored by holding period; black=IWN, gray=SPY, brown=T-bill.)\n"
    )


def _analysis7(runs: dict) -> str:
    reps = ["N5_thr60_hold30", "N10_thr60_hold90", "N10_thr90_hold30", "N10_thr75_hold180"]
    reps = [r for r in reps if r in runs]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    for ax, lb in zip(axes.flat, reps, strict=False):
        h = runs[lb]["holdings"].drop(columns=["CASH"], errors="ignore")
        h = h.loc[:, (h != 0).any()]
        ax.stackplot(range(len(h.index)), *[h[c].values for c in h.columns], labels=list(h.columns))
        ax.set_xticks(range(len(h.index)))
        ax.set_xticklabels([d.strftime("%b") for d in h.index], fontsize=7)
        ax.set_ylim(0, 1)
        ax.set_title(lb, fontsize=9)
        ax.set_ylabel("weight")
        if len(h.columns) <= 14:
            ax.legend(fontsize=5, ncol=2, loc="upper left")
    fig.suptitle(
        "Analysis 7 — portfolio composition over 2025 (4 representative configs; gap to 1.0 = cash)"
    )
    fig.tight_layout()
    fig.savefig(OUT / "portfolio_composition_timeline.png", dpi=110)
    plt.close(fig)
    return (
        "## Analysis 7 — Holding-count timeline\n"
        + _img("portfolio_composition_timeline.png")
        + "\n(Top-of-stack gap to 1.0 = cash / T-bills.)\n"
    )


def _interpretation(runs: dict, labels: list[str], stats: dict) -> str:
    rets = [runs[lb]["metrics"]["total_return_pct"] for lb in labels]
    r2 = float(stats.get("r2", float("nan")))
    corr = float(stats.get("corr", float("nan")))
    n = int(stats.get("n_trades", 0))
    med_base = float(stats.get("med_base", float("nan")))
    med_ex = float(stats.get("med_ex", float("nan")))
    top3 = float(stats.get("top3_share", float("nan")))
    best = str(stats.get("best", "?"))
    return (
        "## Interpretation\n\n"
        "_Synthesis of the computed numbers above. Honest read: the data shows **no "
        "demonstrated edge** — the scoring model does not predict returns, and the positive "
        "2025 is concentration luck within a survivor-biased universe._\n\n"
        f"**1. Does the composite score differentiate / predict?** **No.** Analysis 5 is the "
        f"core test: entry composite vs realized return has **correlation {corr:.3f}, "
        f"R² {r2:.3f}** over {n} trades — i.e. the score explains ~{r2 * 100:.1f}% of "
        "return variation. The 40/30/20/10 composite **does not rank stocks by future "
        "return** in this sample. (Note: valuation+momentum ran neutral for lack of PIT data, "
        "so what's tested is effectively insider+quality — and even that does not predict.)\n\n"
        f"**2. Do the 18 configs differ?** Returns span [{min(rets):.0f}%, {max(rets):.0f}%]. "
        "The variation is driven by the **entry threshold** (higher → fewer, more concentrated "
        "positions → more single-trade luck); **N (5 vs 10) barely matters** because the "
        "eligible set is tiny (1-8 names) — confirming the Phase-2 concentration finding. The "
        "spread across configs is itself mostly noise (each is one 12-month path).\n\n"
        f"**3/4. Concentration, and is +37% survivor bias?** This is the surprising part. "
        f"Removing the trades in 2025's >+100% monster-winners did **not** reduce the median "
        f"return — it went {med_base:.0f}% → {med_ex:.0f}% (essentially unchanged / slightly "
        "up). So the headline return is **NOT** simply 'held the obvious survivor moonshots.' "
        f"Instead it is **idiosyncratic concentration luck**: the best config (`{best}`) made "
        f"**{top3:.0f}% of its return from just 3 trades**. With a 1-8 name book over 12 months "
        "and a score that doesn't predict (R²≈0), a handful of trades happening to gain drives "
        "the result. Survivor bias is still present (the whole universe is 2026 survivors), but "
        "the dominant story is **luck + concentration, not survivor-moonshot capture, and "
        "definitely not score skill.**\n\n"
        "**5. Believable as edge?** **No.** R²≈0 score, return concentrated in ~3 trades, one "
        "year, 9-44 trades, deflated Sharpes (Analysis 2) all <1, survivor-biased universe. "
        "Every lens points to no demonstrated edge.\n\n"
        "**6. Carry-forward to Phase 4B (with PIT data).** Honestly, **nothing here is "
        "validated.** If proceeding, prefer **mid-threshold, longer-holding, N=10** (more "
        "trades → less single-trade dependence) and treat Phase 4B as the real test: re-run on "
        "PIT membership across 2022-2025 and re-check **Analysis 5's R²** — if the score still "
        "doesn't predict out-of-sample and across regimes, the composite framework itself "
        "needs rethinking, not just the universe.\n"
    )


if __name__ == "__main__":
    main()
