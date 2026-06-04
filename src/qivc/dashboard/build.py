"""
Live forward paper-trade dashboard — data layer (Task 15).

Reads ONLY the forward `qivc paper` JSONL ledger and builds the `PAPER_DATA`
object the HTML renderer consumes. Open positions are marked with CURRENT prices;
closed trades use the exit-date close; the equity curve is reconstructed from real
forward daily closes (ledger start → today, forward only — never backfilled). NO
backtest / 2025 / PIT / synthetic data ever enters here.

All metric math lives here (pure, testable). yfinance access goes through an
injectable `PriceProvider` so tests mock it; the live provider caches raw prices
with a short TTL so repeated runs don't hammer the API.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from qivc.backtest.delisting_returns import delisting_exit_price
from qivc.paper.forward import PaperPosition, PaperRecord

# v3.0 baseline weights (the model being paper-traded); technical shelved at 0.
BASELINE_WEIGHTS = [40, 30, 20, 10, 0]
_FACTOR_ORDER = ["insider", "quality", "valuation", "momentum", "technical"]
_HOLD_RE = __import__("re").compile(r"hold(\d+)")


class PriceProvider(Protocol):
    def daily_closes(
        self, tickers: list[str], start: _dt.date, end: _dt.date
    ) -> dict[str, dict[str, float]]:
        """ticker -> {ISO date: close} over [start, end] (raw prices only)."""
        ...


@dataclass(frozen=True)
class TradeSpan:
    ticker: str
    entry_date: str           # ISO
    exit_date: str | None     # ISO; None = still open
    composite: float
    components: dict[str, float]
    weight: float             # equal-weight book weight (for portfolio contribution)
    inputs: dict[str, float | int | None]  # raw factor inputs at entry (drill-down)


# ---------------------------------------------------------------------------
# Pure metric helpers (tested directly)
# ---------------------------------------------------------------------------
def sample_size_label(n: int) -> tuple[str, str]:
    """(label, chip-class) per the honest-metrics contract: <10 void, 10-29 thin, 30+ ok."""
    if n < 10:
        return "void", "void"
    if n < 30:
        return "thin", "thin"
    return "meaningful", "ok"


def score_return_r2(pts: list[tuple[float, float]]) -> float | None:
    """Pearson R² of (composite score at entry, realized return). None if <3 points."""
    n = len(pts)
    if n < 3:
        return None
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] ** 2 for p in pts)
    syy = sum(p[1] ** 2 for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    den = (n * sxx - sx * sx) * (n * syy - sy * sy)
    if den <= 0:
        return 0.0
    r = (n * sxy - sx * sy) / math.sqrt(den)
    return round(r * r, 4)


def top3_concentration(returns: list[float]) -> float:
    """Top-3 winners' return / sum of all positive returns, in % (0 if no winners)."""
    pos = sorted((r for r in returns if r > 0), reverse=True)
    total = sum(pos)
    if total <= 0:
        return 0.0
    return min(sum(pos[:3]) / total * 100, 100.0)


def reconstruct_trades(records: list[PaperRecord]) -> tuple[list[TradeSpan], list[TradeSpan]]:
    """
    Walk dated snapshots in order; a (ticker, entry_date) span closes the first
    snapshot it is absent from (exit = that as_of). Spans in the last snapshot are
    open. Pure — no prices.
    """
    recs = sorted(records, key=lambda r: r.as_of)
    closed: list[TradeSpan] = []
    prev: dict[tuple[str, str], PaperPosition] = {}
    for rec in recs:
        cur = {(p.ticker, p.entry_date): p for p in rec.positions}
        for key, p in prev.items():
            if key not in cur:
                closed.append(_span(p, rec.as_of))
        prev = cur
    open_ = [_span(p, None) for p in prev.values()]
    return closed, open_


def _span(p: PaperPosition, exit_date: str | None) -> TradeSpan:
    return TradeSpan(
        p.ticker, p.entry_date, exit_date, p.composite, dict(p.components), p.weight,
        dict(p.inputs),
    )


def _factor_vec(components: dict[str, float]) -> list[float]:
    return [round(float(components.get(k, 0.0)), 3) for k in _FACTOR_ORDER]


def _hold_target(config: str) -> int:
    m = _HOLD_RE.search(config)
    return int(m.group(1)) if m else 30


# ---------------------------------------------------------------------------
# Ledger read + assembly
# ---------------------------------------------------------------------------
def read_records(ledger_path: str | Path, config: str | None = None) -> list[PaperRecord]:
    p = Path(ledger_path)
    if not p.exists():
        return []
    out: list[PaperRecord] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if config is not None and r.get("config") != config:
            continue
        out.append(
            PaperRecord(
                as_of=r["as_of"], config=r["config"], regime=r["regime"],
                equity_pct=r["equity_pct"], n_scored=r["n_scored"],
                positions=[PaperPosition(**pos) for pos in r["positions"]],
                regime_source=r.get("regime_source", "fred_live"),
                disclaimer=r.get("disclaimer", PaperRecord.disclaimer),
            )
        )
    return out


def _close_on(series: dict[str, float], on: str) -> float | None:
    """Most recent close on-or-before ISO date *on*."""
    keys = [d for d in series if d <= on]
    return series[max(keys)] if keys else None


def _live(series: dict[str, float]) -> float | None:
    return series[max(series)] if series else None


def build_dashboard_data(
    ledger_path: str | Path,
    config: str,
    *,
    price_provider: PriceProvider,
    today: _dt.date,
    delisting_lookup: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Assemble the PAPER_DATA dict from the live ledger (+ live marks)."""
    records = read_records(ledger_path, config)
    meta = {
        "weights": BASELINE_WEIGHTS, "config": config,
        "started": records[0].as_of if records else None,
        "asOf": today.isoformat(), "regimeSource": "FRED (captured per snapshot)",
        "holdTarget": _hold_target(config),
    }
    if not records:
        return _empty(meta)

    closed_spans, open_spans = reconstruct_trades(records)
    ledger_start = records[0].as_of
    hold_tgt = _hold_target(config)
    tickers = sorted({s.ticker for s in closed_spans + open_spans})
    closes = price_provider.daily_closes(
        [*tickers, "IWN", "^IRX"], _dt.date.fromisoformat(ledger_start), today
    )

    closed = [
        _priced_closed(s, closes.get(s.ticker, {}), delisting_lookup) for s in closed_spans
    ]
    open_ = [
        _priced_open(s, closes.get(s.ticker, {}), hold_tgt, today, delisting_lookup)
        for s in open_spans
    ]
    equity = _equity_series(records, closes, ledger_start, today)
    regime = _regime_strip(records)

    realized = [c["ret"] for c in closed]
    n = len(closed)
    label, chip = sample_size_label(n)
    iwn_end = equity[-1]["iwn"] if equity else 0.0
    paper_a = equity[-1]["paper"] if equity else 0.0
    # realized-on-close cumulative (mode B): sum weight x return over closed trades
    paper_b = round(sum(c["wret"] for c in closed), 2)
    headline = {
        "A": {"ret": round(paper_a, 2), "bench": round(paper_a - iwn_end, 2),
              "sub": "incl. open marked-to-market"},
        "B": {"ret": paper_b, "bench": round(paper_b - iwn_end, 2),
              "sub": "realized on close only"},
        "n": n, "nLabel": label, "nChip": chip,
        "conc": round(top3_concentration(realized)),
        "r2": score_return_r2([(c["score"], c["ret"]) for c in closed]),
    }
    return {
        "meta": meta, "headline": headline, "closed": closed, "open": open_,
        "equity": equity, "regime": regime,
    }


def _empty(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "meta": meta,
        "headline": {"A": {"ret": 0.0, "bench": 0.0, "sub": "incl. open marked-to-market"},
                     "B": {"ret": 0.0, "bench": 0.0, "sub": "realized on close only"},
                     "n": 0, "nLabel": "void", "nChip": "void", "conc": 0, "r2": None},
        "closed": [], "open": [], "equity": [], "regime": [],
    }


def _delisting_mark(
    ticker: str, last_px: float, start: str, end: str,
    delisting_lookup: Callable[[str], Any] | None,
) -> tuple[float, str | None]:
    """Return (exit_or_mark, reason|None) applying conservative B3 if delisted in window."""
    if delisting_lookup is None:
        return last_px, None
    ev = delisting_lookup(ticker)
    if ev is None:
        return last_px, None
    fd = ev.filed_date.isoformat() if hasattr(ev.filed_date, "isoformat") else str(ev.filed_date)
    if start < fd <= end:
        return round(delisting_exit_price(ev.reason, last_px), 4), ev.reason
    return last_px, None


def _priced_closed(
    s: TradeSpan, series: dict[str, float], delisting_lookup: Callable[[str], Any] | None
) -> dict[str, Any]:
    entry = _close_on(series, s.entry_date)
    assert s.exit_date is not None
    exit_px = _close_on(series, s.exit_date)
    reason = None
    if exit_px is not None:
        exit_px, reason = _delisting_mark(
            s.ticker, exit_px, s.entry_date, s.exit_date, delisting_lookup
        )
    ret = ((exit_px / entry - 1) * 100) if (entry and exit_px) else 0.0
    hold = (_dt.date.fromisoformat(s.exit_date) - _dt.date.fromisoformat(s.entry_date)).days
    return {
        "ticker": s.ticker, "entry": s.entry_date, "exit": s.exit_date,
        "entryPx": round(entry, 2) if entry else None,
        "exitPx": round(exit_px, 2) if exit_px else None,
        "ret": round(ret, 2), "hold": hold, "score": round(s.composite, 3),
        "f": _factor_vec(s.components),
        "weight": round(s.weight, 4),
        "wret": round(s.weight * ret, 4),  # portfolio contribution % (weight x return)
        "delisted": reason,
    }


def _priced_open(
    s: TradeSpan, series: dict[str, float], hold_tgt: int, today: _dt.date,
    delisting_lookup: Callable[[str], Any] | None,
) -> dict[str, Any]:
    entry = _close_on(series, s.entry_date)
    mark = _live(series)
    reason = None
    if mark is not None:
        mark, reason = _delisting_mark(
            s.ticker, mark, s.entry_date, today.isoformat(), delisting_lookup
        )
    unreal = ((mark / entry - 1) * 100) if (entry and mark) else 0.0
    hold_day = (today - _dt.date.fromisoformat(s.entry_date)).days
    return {
        "ticker": s.ticker, "entry": s.entry_date,
        "entryPx": round(entry, 2) if entry else None,
        "mark": round(mark, 2) if mark else None,
        "holdDay": hold_day, "holdTgt": hold_tgt, "ret": round(unreal, 2),
        "score": round(s.composite, 3), "f": _factor_vec(s.components), "delisted": reason,
    }


def _cum_pct(series: dict[str, float], dates: list[str]) -> dict[str, float]:
    """Cumulative % vs the first date's close, for each date in *dates*."""
    base = _close_on(series, dates[0]) if dates else None
    out: dict[str, float] = {}
    for d in dates:
        px = _close_on(series, d)
        out[d] = round((px / base - 1) * 100, 3) if (base and px) else 0.0
    return out


def _equity_series(
    records: list[PaperRecord], closes: dict[str, dict[str, float]],
    ledger_start: str, today: _dt.date,
) -> list[dict[str, Any]]:
    """
    Forward-only cumulative-% curve (paper vs IWN vs cash). Sampled weekly. paper =
    equal-weighted daily return of the active monthly book (live-marked) + cash; iwn
    from IWN closes; cash from ^IRX accrual. Empty if too little data.
    """
    iwn = closes.get("IWN", {})
    trading_days = sorted(d for d in iwn if ledger_start <= d <= today.isoformat())
    if len(trading_days) < 2:
        return []
    # weekly sample (every 5 trading days) + always the last day
    sample = trading_days[::5]
    if trading_days[-1] not in sample:
        sample.append(trading_days[-1])

    iwn_cum = _cum_pct(iwn, sample)
    # cash: ^IRX annualized %, accrued daily
    irx = closes.get("^IRX", {})
    recs = sorted(records, key=lambda r: r.as_of)

    def active_book(day: str) -> list[PaperPosition]:
        book: list[PaperPosition] = []
        for r in recs:
            if r.as_of <= day:
                book = r.positions
        return book

    out: list[dict[str, Any]] = []
    paper_cum = 0.0
    cash_cum = 0.0
    prev_day: str | None = None
    for day in sample:
        if prev_day is not None:
            # paper daily return over the prior active book, equal-weighted by weight
            book = active_book(prev_day)
            day_ret = 0.0
            for p in book:
                ser = closes.get(p.ticker, {})
                a, b = _close_on(ser, prev_day), _close_on(ser, day)
                if a and b:
                    day_ret += p.weight * (b / a - 1)
            rate = (_close_on(irx, day) or 0.0) / 100.0
            days_elapsed = (_dt.date.fromisoformat(day) - _dt.date.fromisoformat(prev_day)).days
            cash_w = max(0.0, 1.0 - sum(p.weight for p in book))
            day_ret += cash_w * rate * days_elapsed / 365.0
            paper_cum = (1 + paper_cum / 100) * (1 + day_ret) * 100 - 100
            cash_cum += rate * days_elapsed / 365.0 * 100
        out.append({
            "iso": day, "date": day[5:], "paper": round(paper_cum, 2),
            "iwn": round(iwn_cum.get(day, 0.0), 2), "cash": round(cash_cum, 2),
        })
        prev_day = day
    return out


def _regime_strip(records: list[PaperRecord]) -> list[dict[str, str]]:
    """Per-month regime from the ledger-captured regime field (no live FRED call)."""
    seen: dict[str, str] = {}
    for r in sorted(records, key=lambda r: r.as_of):
        d = _dt.date.fromisoformat(r.as_of)
        key = d.strftime("%b %y")
        seen[key] = r.regime  # last snapshot of the month wins
    return [{"month": k, "state": v} for k, v in seen.items()]
