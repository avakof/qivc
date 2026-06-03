#!/usr/bin/env python
"""
Phase 4A winner/loser DESCRIPTIVE analysis for the v3_2025_N10_thr90_hold30 run.

    uv run python scripts/analyze_4a_winners_losers.py

This is EXPLORATORY, NON-VALIDATION analysis. It builds a per-trade feature table
for the 28 trades of the single config N10_thr90_hold30, groups winners vs losers,
and surfaces DESCRIPTIVE patterns only (no statistical tests — n=22 closed trades
is far below any significance floor). The output is a list of HYPOTHESES for future
testing under proper point-in-time cross-validation, NOT validated features.

Data sources:
  - trades:      data/backtests/v3_2025_N10_thr90_hold30/trades.csv (existing run)
  - composite + components: re-scored per entry month from the warm v3 cache
                 (data/backtests/_cache_v3_2025) + local bulk store (NO new fetch)
  - sector:      iwm_sector_map() (committed snapshot)
  - mcap@entry, 2024 return: yfinance ONE-TIME context fetch for the 25 tickers,
                 cached to data/backtests/analysis_4A/context_yf.json. These are
                 CONTEXT columns (not backtest inputs). mcap@entry uses CURRENT
                 shares outstanding x entry price (approximate; shares drift).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from qivc.backtest import providers
from qivc.backtest.composite_scorer import score_universe
from qivc.backtest.universe import iwm_sector_map, iwm_tickers
from qivc.backtest.v3_factors import assemble_factors
from qivc.config import Settings

CONFIG = "N10_thr90_hold30"
RUN_DIR = Path(f"data/backtests/v3_2025_{CONFIG}")
OUT_DIR = Path("data/backtests/analysis_4A")
CACHE_DIR = Path("data/backtests/_cache_v3_2025")
EXCLUDE = {"Financials", "Real Estate"}
DOC = Path("BACKTEST_ANALYSIS_4A_WINNERS.md")


def rescore_components() -> dict[dt.date, dict[str, dict]]:
    """ticker -> {composite, insider, quality, valuation, momentum} per entry month."""
    s = Settings()
    sector_map = iwm_sector_map()
    universe = {t for t in iwm_tickers() if sector_map.get(t) not in EXCLUDE}
    fp = providers.make_fundamentals_provider(providers.BacktestCache(CACHE_DIR))
    holdings = pd.read_csv(RUN_DIR / "holdings.csv", index_col=0, parse_dates=True)
    out: dict[dt.date, dict[str, dict]] = {}
    for ts in holdings.index:
        d = ts.date()
        factors = assemble_factors(
            d, db_path=s.db_path, universe=universe, sector_map=sector_map,
            fundamentals_provider=fp,
        )
        scored = score_universe(factors)
        out[d] = {
            sc.ticker: {"composite": sc.composite, **sc.components} for sc in scored
        }
    return out


def fetch_context(tickers: list[str]) -> dict[str, dict]:
    """yfinance ONE-TIME context fetch (cached): 2024 full-year return + shares."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache = OUT_DIR / "context_yf.json"
    if cache.exists():
        return json.loads(cache.read_text())
    import yfinance as yf

    ctx: dict[str, dict] = {}
    px = yf.download(
        tickers, start="2024-01-01", end="2025-01-05", progress=False, auto_adjust=True
    )
    close = px.get("Close", px)
    for t in tickers:
        ret_2024: float | None = None
        if t in close.columns:
            col = close[t].dropna()
            y24 = col[(col.index >= "2024-01-01") & (col.index < "2025-01-01")]
            if len(y24) >= 2:
                ret_2024 = float(y24.iloc[-1] / y24.iloc[0] - 1.0)
        shares = None
        try:
            shares = yf.Ticker(t).info.get("sharesOutstanding")
        except Exception:  # context only — yfinance flakiness is non-fatal
            shares = None
        ctx[t] = {"ret_2024": ret_2024, "shares_outstanding": shares}
    cache.write_text(json.dumps(ctx, indent=1))
    return ctx


def build_table() -> pd.DataFrame:
    trades = pd.read_csv(RUN_DIR / "trades.csv")
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    trades = trades.sort_values("entry_date").reset_index(drop=True)
    sector_map = iwm_sector_map()
    comps = rescore_components()
    ctx = fetch_context(sorted(trades["ticker"].unique()))

    seen: dict[str, int] = {}          # ticker -> count of prior trades
    prior_loss: dict[str, bool] = {}   # ticker -> was the immediately-prior trade a loss

    rows = []
    for _, t in trades.iterrows():
        tic = t["ticker"]
        d = t["entry_date"].date()
        c = comps.get(d, {}).get(tic, {})
        r = t["return_pct"]
        is_open = pd.isna(t["exit_date"])
        n_prior = seen.get(tic, 0)
        sh = (ctx.get(tic) or {}).get("shares_outstanding")
        mcap = float(sh) * float(t["entry_price"]) if sh else None
        rows.append({
            "ticker": tic,
            "sector": sector_map.get(tic, "Unknown"),
            "entry_month": t["entry_date"].strftime("%Y-%m"),
            "hold_days": None if is_open else int(t["hold_days"]),
            "return_pct": None if is_open else round(float(r) * 100, 2),
            "outcome": "OPEN" if is_open else ("WIN" if r > 0 else "LOSS"),
            "composite": round(c.get("composite", float("nan")), 3),
            "insider": round(c.get("insider", float("nan")), 3),
            "quality": round(c.get("quality", float("nan")), 3),
            "valuation": round(c.get("valuation", float("nan")), 3),
            "momentum": round(c.get("momentum", float("nan")), 3),
            "prior_trades_2025": n_prior,
            "prior_was_loss": prior_loss.get(tic) if n_prior else None,
            "mcap_entry_$M": round(mcap / 1e6, 0) if mcap else None,
            "ret_2024_pct": (
                round((ctx.get(tic) or {}).get("ret_2024") * 100, 1)
                if (ctx.get(tic) or {}).get("ret_2024") is not None else None
            ),
        })
        seen[tic] = n_prior + 1
        if not is_open:
            prior_loss[tic] = r <= 0
    return pd.DataFrame(rows)


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    head = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = []
    for _, row in df.iterrows():
        body.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |")
    return "\n".join([head, sep, *body])


def main() -> None:
    df = build_table()
    closed = df[df["outcome"] != "OPEN"].copy()
    wins = closed[closed["outcome"] == "WIN"]
    losses = closed[closed["outcome"] == "LOSS"]
    open_t = df[df["outcome"] == "OPEN"]

    def summ(g: pd.DataFrame) -> dict:
        return {
            "n": len(g),
            "median_composite": round(g["composite"].median(), 3),
            "median_insider": round(g["insider"].median(), 3),
            "median_quality": round(g["quality"].median(), 3),
            "median_hold": round(g["hold_days"].median(), 1) if "hold_days" in g else None,
            "median_mcap_$M": round(g["mcap_entry_$M"].median(), 0),
            "median_ret2024_pct": round(g["ret_2024_pct"].median(), 1),
        }

    print("WINNERS:", summ(wins))
    print("LOSERS :", summ(losses))

    L = []
    L.append("# Phase 4A — Winner/Loser DESCRIPTIVE Analysis "
             f"(`v3_2025_{CONFIG}`)\n")
    L.append("> ⚠️ **THIS IS NOT VALIDATION.** Exploratory description of a single "
             "config's 28 trades from one survivor-biased year. **n = 22 closed "
             "trades** — below any statistical-significance floor. No hypothesis "
             "tests are run or implied. Every pattern below is a **HYPOTHESIS to "
             "test later** under point-in-time cross-validation, not a feature, not "
             "evidence of edge. Recall Task 11: composite-vs-return R^2 = 0.004.\n")

    L.append("## Why this is not validation (read first)\n")
    L.append("- **Survivor/membership bias:** universe = 2026-06 IWM snapshot applied "
             "to 2025; names that delisted are absent. Winners are conditioned on "
             "survival.\n"
             "- **n=22 closed** (12 win / 10 loss): one moderate winner moves every "
             "median. No power to separate signal from noise.\n"
             "- **One year, one regime** (2025 small-cap tape). No cross-section over "
             "time.\n"
             "- **mcap@entry approximate:** current shares x entry price (shares "
             "drift). `ret_2024` is backward context, not a tradeable signal.\n"
             "- **valuation & momentum run neutral (0.5)** in v3.0 — their columns "
             "carry no information here; do not read them.\n")

    L.append("## Full feature table (28 trades, entry order)\n")
    L.append(_md_table(df) + "\n")

    L.append("## Grouped: winners\n")
    L.append(_md_table(wins.drop(columns=["outcome"])) + "\n")
    L.append("## Grouped: losers\n")
    L.append(_md_table(losses.drop(columns=["outcome"])) + "\n")
    if len(open_t):
        L.append("## Still open at year-end (excluded from win/loss)\n")
        L.append(_md_table(open_t.drop(columns=["outcome"])) + "\n")

    L.append("## Group medians (DESCRIPTIVE — do not infer)\n")
    sm = pd.DataFrame({"winners": summ(wins), "losers": summ(losses)}).T
    L.append(_md_table(sm.reset_index().rename(columns={"index": "group"})) + "\n")

    L.append("## Descriptive patterns visible (HYPOTHESES — no tests, n is tiny)\n")
    L.append(
        "Each item is something the eye picks out in 22 closed trades. None is "
        "tested; each is a **candidate to test under PIT cross-validation**, not a "
        "finding.\n\n"
        "1. **The composite does not separate winners from losers.** Median "
        f"composite is {summ(wins)['median_composite']} (win) vs "
        f"{summ(losses)['median_composite']} (loss) — effectively identical. This "
        "is the same message as Task 11's R^2=0.004, now visible at the trade "
        "level: ranking by composite did not rank by outcome.\n"
        "2. **Winners and losers reach the same composite via OPPOSITE factor "
        f"mixes.** Winners: high insider (median {summ(wins)['median_insider']}) / "
        f"low quality (median {summ(wins)['median_quality']}). Losers: lower "
        f"insider ({summ(losses)['median_insider']}) / high quality "
        f"({summ(losses)['median_quality']}). The 40/30 blend averaged a possible "
        "insider-conviction tilt away. *Hypothesis:* insider conviction may carry "
        "more signal than the 40% weight captures, and Piotroski/GP-A quality may "
        "be miscalibrated — or even contrarian — in this small-cap insider "
        "universe. (Counterexamples exist: STAA, CDXS, MDGL are high-insider "
        "losers — so this is weak and noisy.)\n"
        "3. **Many winners were beaten-down in 2024.** CVI (-37%), JELD (-56%), "
        "RIG (-40%), KALV (-30%) all fell hard in 2024, drew insider buying, then "
        "rose in 2025. Both groups skew negative-2024 (winners median "
        f"{summ(wins)['median_ret2024_pct']}%, losers "
        f"{summ(losses)['median_ret2024_pct']}%). *Hypothesis:* "
        "beaten-down-prior-year + insider buying => mean-reversion bounce. "
        "(Counterexample: SLSN +307% in 2024 -> -36% loss.)\n"
        "4. **Winners skew larger-cap** (median mcap "
        f"~${summ(wins)['median_mcap_$M']:.0f}M vs "
        f"~${summ(losses)['median_mcap_$M']:.0f}M). *Hypothesis:* a size effect — "
        "but MDGL (~$10B) was a loser, so this is fragile.\n"
        "5. **Hold days and sector show no visible separation.** Median hold is "
        "60.5 days for both groups. Energy and Health Care each appear among both "
        "winners and losers.\n"
    )

    # Repeat-entry-after-loss
    L.append("## Repeat-entry pattern (BATRA, TDW)\n")
    rep = df[df["ticker"].isin(["BATRA", "TDW"])][
        ["ticker", "entry_month", "outcome", "return_pct", "composite",
         "insider", "prior_trades_2025", "prior_was_loss"]
    ]
    L.append(_md_table(rep) + "\n")
    L.append(
        "Only **two names repeat**, for **three re-entries total**. The strategy "
        "has **no outcome memory** — it re-buys whenever a name re-qualifies on "
        "fresh insider activity, regardless of how the prior trade ended.\n\n"
        "- **BATRA:** Feb LOSS (-0.5%) -> May WIN (+0.9%, marginal) -> Dec OPEN. "
        "Its insider score rose 0.75 -> 0.97 by the May re-entry (fresh buying).\n"
        "- **TDW:** Apr LOSS (-6.3%) -> Jul LOSS (-0.4%). Re-entered after a loss "
        "and lost again; its insider score *fell* 0.75 -> 0.64.\n\n"
        "Both after-loss re-entries landed near zero (+0.9%, -0.4%). *Hypothesis:* "
        "a re-entry-after-loss rule (skip, or size down, a name that just lost) "
        "might be worth testing — but **n=2 re-entries proves nothing**; this is "
        "an observation to revisit with many years of PIT data, not a signal.\n"
    )
    DOC.write_text("\n".join(L) + "\n")
    print(f"\nwrote {DOC}  (winners={len(wins)} losers={len(losses)} open={len(open_t)})")


if __name__ == "__main__":
    main()
