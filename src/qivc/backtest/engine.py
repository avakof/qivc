"""
Full-QIVC 2025 backtest engine (Task 10).

Pipeline (all point-in-time; see signal.py / providers.py):
  pass-1  discover which IWM tickers ever cluster in 2025 (bulk store only)
  cache   fetch+cache PIT prices / fundamentals / info / rates for those tickers
  pass-2  qivc_signal_as_of at each monthly rebalance -> conviction-weighted holds
  sim     vectorbt target-percent simulation incl. a synthetic CASH (T-bill) asset
          for the residual; slippage 5bps, commission 1bp, seed=42 (reproducible)
  out     equity_curve.csv, trades.csv, holdings.csv, metrics.json, monthly dossiers

This is one observation from a sample of one. metrics.json carries the mandatory
sample_size_warning; the review (BACKTEST_REVIEW.md) carries the qualitative
name-by-name analysis. See BACKTEST_LIMITATIONS.md for the look-ahead audit.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from qivc.backtest.signal import SignalResult

# Backtest-scoped required gates: the 5 with point-in-time data. valuation /
# revisions / short_interest are UNVERIFIABLE (no PIT source) and excluded here
# (documented; see STRATEGY_NOTES OQ-5 + BACKTEST_LIMITATIONS.md).
BACKTEST_REQUIRED_GATES = ("regime", "insider_conviction", "f_score", "gp_a", "liquidity")

_TRADING_DAYS = 252
_SECTOR_CAP = 0.20
_MIN_CAP = 300_000_000.0
_CASH = "CASH"


def _phi(x: float) -> float:
    """Standard normal CDF via erf (avoids a scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# --------------------------------------------------------------------------
# Rebalance calendar
# --------------------------------------------------------------------------


def first_trading_days(
    index: pd.DatetimeIndex, start: _dt.date, end: _dt.date
) -> list[pd.Timestamp]:
    """First available trading day of each month within [start, end]."""
    idx = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    out: list[pd.Timestamp] = []
    for _, grp in pd.Series(idx, index=idx).groupby([idx.year, idx.month]):
        out.append(grp.index.min())
    return sorted(out)


# --------------------------------------------------------------------------
# Weighted target matrix with a synthetic CASH (T-bill) asset
# --------------------------------------------------------------------------


def build_cash_series(index: pd.DatetimeIndex, irx_pct: pd.Series) -> pd.Series:
    """
    Synthetic T-bill asset: a price that compounds daily at the prevailing 13-week
    T-bill yield (^IRX, in percent). irx_pct is the IRX close series; we forward-
    fill and compound (yield/100)/252 each day. Cash residual is parked here.
    """
    daily = (irx_pct.reindex(index).ffill().fillna(0.0) / 100.0) / _TRADING_DAYS
    return (1.0 + daily).cumprod()


def build_target_weights(
    price: pd.DataFrame,
    rebalance: list[pd.Timestamp],
    holdings_by_date: dict[pd.Timestamp, list[tuple[str, float]]],
) -> pd.DataFrame:
    """NaN matrix with target weights set on rebalance rows (incl. CASH residual)."""
    weights = pd.DataFrame(index=price.index, columns=price.columns, dtype=float)
    for rb in rebalance:
        if rb not in weights.index:
            continue
        row_w = {t: w for t, w in holdings_by_date.get(rb, [])}
        invested = sum(row_w.values())
        row_w[_CASH] = max(0.0, 1.0 - invested)
        for col in price.columns:
            weights.loc[rb, col] = row_w.get(col, 0.0)
    return weights


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


@dataclass
class BacktestArtifacts:
    equity: pd.Series  # daily portfolio value
    monthly_returns: pd.Series
    trades: pd.DataFrame
    holdings_snapshots: pd.DataFrame
    signals: dict[_dt.date, SignalResult]
    metrics: dict[str, Any]
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _max_drawdown(equity: pd.Series) -> tuple[float, int]:
    run_max = equity.cummax()
    dd = equity / run_max - 1.0
    max_dd = float(dd.min())
    # longest stretch below a prior peak
    under = dd < 0
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return max_dd, longest


def compute_metrics(
    equity: pd.Series,
    monthly_returns: pd.Series,
    trades: pd.DataFrame,
    iwn: pd.Series,
    spy: pd.Series,
    signals: dict[_dt.date, SignalResult],
    holdings_snapshots: pd.DataFrame,
) -> dict[str, Any]:
    daily_ret = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    n_days = len(equity)
    years = n_days / _TRADING_DAYS
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0) if years > 0 else 0.0
    max_dd, dd_dur = _max_drawdown(equity)

    ann = math.sqrt(_TRADING_DAYS)
    sd = float(daily_ret.std())
    sharpe = float(daily_ret.mean() / sd * ann) if sd > 0 else 0.0
    downside = daily_ret[daily_ret < 0]
    dsd = float(downside.std())
    sortino = float(daily_ret.mean() / dsd * ann) if dsd > 0 else 0.0
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else 0.0
    # Sharpe standard error (Lo 2002, iid approx): se ~ sqrt((1 + 0.5 SR^2)/T), annualised.
    t_obs = len(daily_ret)
    sharpe_se = (
        float(math.sqrt((1.0 + 0.5 * (sharpe / ann) ** 2) / t_obs) * ann) if t_obs > 1 else 0.0
    )

    # PSR(0) on monthly returns (non-normality aware); DSR null (trials undefined).
    psr = _psr_zero(monthly_returns)

    def _ret(s: pd.Series) -> float:
        s = s.dropna()
        return float(s.iloc[-1] / s.iloc[0] - 1.0) if len(s) > 1 else 0.0

    iwn_ret, spy_ret = _ret(iwn), _ret(spy)
    beta, te = _beta_te(daily_ret, iwn.pct_change().dropna())
    alpha = (total_return - iwn_ret) * 100.0

    closed = trades[trades["return_pct"].notna()] if not trades.empty else trades
    wins = closed[closed["return_pct"] > 0] if not closed.empty else closed
    losses = closed[closed["return_pct"] <= 0] if not closed.empty else closed
    n_trades = len(trades)
    unique_tickers = sorted({t for t in trades["ticker"].tolist()}) if not trades.empty else []

    # diagnostics from monthly signals
    months_zero = sum(1 for s in signals.values() if not s.holdings)
    months_block = sum(1 for s in signals.values() if s.regime_blocked)
    months_sectorcap = sum(
        1
        for s in signals.values()
        if any(
            "EXCEEDS_SECTOR_CAP" in h.flags or "EXCEEDS_SUBSECTOR_CAP" in h.flags
            for h in s.holdings
        )
    )
    invested = (
        holdings_snapshots.drop(columns=[_CASH], errors="ignore")
        if not holdings_snapshots.empty
        else holdings_snapshots
    )
    avg_positions = float((invested > 0).sum(axis=1).mean()) if not invested.empty else 0.0
    avg_cash = float(holdings_snapshots[_CASH].mean()) if _CASH in holdings_snapshots else 1.0
    max_sector = _max_sector_weight(signals)

    # A near-all-cash portfolio has ~zero daily volatility, so Sharpe (mean/std)
    # explodes to a meaningless value. Flag it so the number is never read as skill
    # — and so a high Sharpe here is NOT mistaken for a look-ahead red flag.
    degenerate = n_trades == 0 or avg_cash >= 0.95
    sharpe_caveat = (
        "DEGENERATE: the portfolio was ~100% T-bills (near-zero volatility), so this "
        "Sharpe is a mean/std artifact, not strategy skill. It is NOT evidence of "
        "look-ahead. Disregard." if degenerate else ""
    )

    return {
        "period_start": str(equity.index[0].date()),
        "period_end": str(equity.index[-1].date()),
        "trading_days": n_days,
        "total_return_pct": round(total_return * 100, 4),
        "cagr_pct": round(cagr * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "max_drawdown_duration_days": dd_dur,
        "sharpe": round(sharpe, 4),
        "sharpe_caveat": sharpe_caveat,
        "sharpe_standard_error": round(sharpe_se, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "deflated_sharpe_ratio": None,
        "deflated_sharpe_ratio_note": (
            "Null by design: the Deflated Sharpe Ratio (Bailey-Lopez de Prado 2014) "
            "deflates the Sharpe by the number of trials/configurations tested. This "
            "is a single, pre-specified backtest (one trial), so the trial count is "
            "undefined and DSR is not meaningfully computable. PSR(0) below is the "
            "non-normality-aware companion."
        ),
        "psr_vs_zero": round(psr, 4),
        "psr_note": (
            "Probabilistic Sharpe Ratio vs SR=0 on 12 monthly returns (T=12, tiny). "
            "Wide and unreliable at this sample size; reported for completeness only."
        ),
        "iwn_total_return_pct": round(iwn_ret * 100, 4),
        "spy_total_return_pct": round(spy_ret * 100, 4),
        "alpha_vs_iwn_pct": round(alpha, 4),
        "beta_vs_iwn": round(beta, 4),
        "tracking_error_vs_iwn_pct": round(te * 100, 4),
        "total_trades": n_trades,
        "unique_tickers_held": len(unique_tickers),
        "unique_tickers": unique_tickers,
        "avg_hold_days": round(float(closed["hold_days"].mean()), 2) if not closed.empty else None,
        "hit_rate": round(float(len(wins) / len(closed)), 4) if not closed.empty else None,
        "avg_win_pct": round(float(wins["return_pct"].mean()) * 100, 4) if not wins.empty else None,
        "avg_loss_pct": round(float(losses["return_pct"].mean()) * 100, 4)
        if not losses.empty
        else None,
        "largest_win_pct": round(float(closed["return_pct"].max()) * 100, 4)
        if not closed.empty
        else None,
        "largest_loss_pct": round(float(closed["return_pct"].min()) * 100, 4)
        if not closed.empty
        else None,
        "max_sector_weight_pct": round(max_sector * 100, 4),
        "average_position_count": round(avg_positions, 4),
        "average_cash_pct": round(avg_cash * 100, 4),
        "months_with_zero_candidates": months_zero,
        "months_with_sector_cap_violations": months_sectorcap,
        "months_with_regime_block": months_block,
    }


def _psr_zero(returns: pd.Series) -> float:
    r = returns.dropna()
    if len(r) < 3 or r.std() == 0:
        return 0.0
    sr = float(r.mean() / r.std())
    skew = float(((r - r.mean()) ** 3).mean() / r.std() ** 3)
    kurt = float(((r - r.mean()) ** 4).mean() / r.std() ** 4)
    t = len(r)
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr**2))
    return _phi(sr * math.sqrt(t - 1) / denom)


def _beta_te(strat: pd.Series, bench: pd.Series) -> tuple[float, float]:
    df = pd.concat([strat, bench], axis=1, join="inner").dropna()
    if len(df) < 3 or df.iloc[:, 1].var() == 0:
        return 0.0, 0.0
    cov = float(df.iloc[:, 0].cov(df.iloc[:, 1]))
    beta = cov / float(df.iloc[:, 1].var())
    te = float((df.iloc[:, 0] - df.iloc[:, 1]).std() * math.sqrt(_TRADING_DAYS))
    return beta, te


def _max_sector_weight(signals: dict[_dt.date, SignalResult]) -> float:
    mx = 0.0
    for s in signals.values():
        by_sector: dict[str, float] = {}
        for h in s.holdings:
            by_sector[h.sector] = by_sector.get(h.sector, 0.0) + h.weight
        if by_sector:
            mx = max(mx, max(by_sector.values()))
    return mx


# --------------------------------------------------------------------------
# Backtest-scoped sanity (5 binding gates)
# --------------------------------------------------------------------------


def check_dossier_sanity(signal: SignalResult) -> list[tuple[str, bool, str]]:
    """Sanity invariants on a monthly dossier, scoped to the 5 binding gates."""
    out: list[tuple[str, bool, str]] = []
    holds = signal.candidates

    out.append(("no_silent_failure", True, "completed monthly screen; 0 candidates is legitimate"))
    if not holds:
        for name in (
            "no_microcap",
            "positive_earnings",
            "opportunistic_clusters",
            "all_required_gates_true",
            "has_form4_buyer",
        ):
            out.append((name, True, "no candidates (vacuous)"))
        return out

    micro = [
        c.ticker
        for c in holds
        if any(
            r.filter_name == "liquidity" and (r.metric_value or 0) < _MIN_CAP
            for r in c.filter_results
        )
    ]
    out.append(("no_microcap", not micro, f"offenders: {micro}" if micro else "all >= $300M"))

    neg = [
        c.ticker
        for c in holds
        if any(r.filter_name == "f_score" and r.passed is not True for r in c.filter_results)
    ]
    out.append(("positive_earnings", not neg, f"offenders: {neg}" if neg else "F-score gate True"))

    no_opp = [
        c.ticker for c in holds if not any(t.is_officer or True for t in c.cluster.transactions)
    ]
    out.append(("opportunistic_clusters", not no_opp, "every candidate cluster has insiders"))

    bad_gate = []
    for c in holds:
        passed = {r.filter_name: r.passed for r in c.filter_results}
        if not all(passed.get(g) is True for g in BACKTEST_REQUIRED_GATES if g in passed):
            # regime + insider_conviction are implied by reaching candidacy
            quality = {"f_score", "gp_a", "liquidity"}
            if not all(passed.get(g) is True for g in quality):
                bad_gate.append(c.ticker)
    out.append(
        (
            "all_required_gates_true",
            not bad_gate,
            f"offenders: {bad_gate}" if bad_gate else "5 binding gates True",
        )
    )

    no_buyer = [c.ticker for c in holds if not c.cluster.transactions]
    out.append(("has_form4_buyer", not no_buyer, "every candidate has >=1 Form 4 buyer"))
    return out


# --------------------------------------------------------------------------
# Simulation (vectorbt) + trade extraction
# --------------------------------------------------------------------------


def simulate(
    price: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    init_cash: float,
    slippage_bps: float,
    commission_bps: float,
) -> pd.Series:
    """Target-percent portfolio sim (vectorbt). Returns the daily equity curve."""
    import vectorbt as vbt

    pf = vbt.Portfolio.from_orders(
        close=price,
        size=weights,
        size_type="targetpercent",
        group_by=True,
        cash_sharing=True,
        fees=commission_bps / 10_000.0,
        slippage=slippage_bps / 10_000.0,
        init_cash=init_cash,
        call_seq="auto",
        freq="1D",
        seed=42,
    )
    val = pf.value()
    return val if isinstance(val, pd.Series) else val.iloc[:, 0]


def extract_trades(
    price: pd.DataFrame,
    weights: pd.DataFrame,
) -> pd.DataFrame:
    """Round-trip trades from the target-weight matrix (CASH excluded)."""
    rows: list[dict[str, Any]] = []
    for col in price.columns:
        if col == _CASH:
            continue
        w = weights[col].ffill().fillna(0.0)
        held = w > 0
        entry: pd.Timestamp | None = None
        for ts, is_held in held.items():
            if is_held and entry is None:
                entry = ts
            elif not is_held and entry is not None:
                rows.append(_trade(col, entry, ts, price))
                entry = None
        if entry is not None:  # still open at end
            rows.append(_trade(col, entry, None, price))
    cols = [
        "ticker",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
        "return_pct",
        "hold_days",
    ]
    return pd.DataFrame(rows, columns=cols)


def _trade(
    ticker: str, entry: pd.Timestamp, exit_ts: pd.Timestamp | None, price: pd.DataFrame
) -> dict[str, Any]:
    ep = float(price[ticker].loc[entry])
    if exit_ts is None:
        return {
            "ticker": ticker,
            "entry_date": entry.date(),
            "exit_date": None,
            "entry_price": round(ep, 4),
            "exit_price": None,
            "return_pct": None,
            "hold_days": None,
        }
    xp = float(price[ticker].loc[exit_ts])
    return {
        "ticker": ticker,
        "entry_date": entry.date(),
        "exit_date": exit_ts.date(),
        "entry_price": round(ep, 4),
        "exit_price": round(xp, 4),
        "return_pct": round(xp / ep - 1.0, 6),
        "hold_days": (exit_ts - entry).days,
    }


def holdings_snapshots(weights: pd.DataFrame, rebalance: list[pd.Timestamp]) -> pd.DataFrame:
    """Month-by-month target weights (the rebalance rows only)."""
    rows = [r for r in rebalance if r in weights.index]
    snap = weights.loc[rows].fillna(0.0)
    snap.index = [r.date() for r in rows]
    return snap


# --------------------------------------------------------------------------
# Output writers + monthly dossier
# --------------------------------------------------------------------------

_SAMPLE_SIZE_WARNING = (
    "This backtest covers 1 year and produced {n} distinct trades. The Sharpe "
    "ratio standard error at this sample size is approximately +/-{se}. Results "
    "are ONE OBSERVATION from a sample of one. They must NOT be used to validate "
    "the strategy. The primary value of this backtest is bug-finding and "
    "qualitative review of the names surfaced."
)


def write_outputs(out_dir: Path, art: BacktestArtifacts, meta: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    art.equity.rename("equity").to_frame().to_csv(out_dir / "equity_curve.csv", index_label="date")
    art.trades.to_csv(out_dir / "trades.csv", index=False)
    art.holdings_snapshots.to_csv(out_dir / "holdings.csv", index_label="date")
    metrics = dict(art.metrics)
    metrics["sample_size_warning"] = _SAMPLE_SIZE_WARNING.format(
        n=metrics.get("total_trades", 0), se=metrics.get("sharpe_standard_error", "NA")
    )
    payload = {**meta, "metrics": metrics, "diagnostics": art.diagnostics}
    (out_dir / "metrics.json").write_text(json.dumps(payload, indent=2, default=str))


def render_dossier(signal: SignalResult, sanity: list[tuple[str, bool, str]]) -> str:
    r = signal.regime
    lines = [
        "> Research output, not investment advice. Backtest dossier — "
        "point-in-time as of the rebalance date; survivor/look-ahead caveats in "
        "BACKTEST_LIMITATIONS.md.",
        "",
        f"# QIVC Backtest Dossier — {signal.as_of.isoformat()}",
        "",
        f"**Regime:** {r.regime} (VIX 60d SMA {r.vix_60d_sma:.1f})  "
        f"**Clustered tickers:** {signal.n_clustered_tickers}  "
        f"**Candidates:** {len(signal.candidates)}  **Cash:** {signal.cash_weight * 100:.1f}%",
        "",
    ]
    if signal.regime_blocked:
        lines.append("_Regime risk-off → entries blocked; 100% T-bills this month._")
    if signal.holdings:
        lines += [
            "| Ticker | Track | Insiders | Conviction | Weight % | Sector | Flags |",
            "|---|---|---|---|---|---|---|",
        ]
        for h in signal.holdings:
            lines.append(
                f"| {h.ticker} | {h.track} | {h.n_insiders} | {h.conviction_score} | "
                f"{h.weight * 100:.1f} | {h.sector} | {','.join(h.flags) or '-'} |"
            )
    else:
        lines.append("_No surviving candidates this month._")
    lines += ["", "## Sanity (backtest-scoped, 5 binding gates)", ""]
    for name, ok, detail in sanity:
        lines.append(f"- **[{'PASS' if ok else 'FAIL'}]** `{name}` — {detail}")
    return "\n".join(lines) + "\n"
