"""
Ticker drill-down data (Task 15.3) — a FACTUAL mechanics readout, not a pitch.

Assembles, for one ticker: the composite score-mechanics breakdown, the raw Form 4
insider evidence (from form4_historical, with CMP classification), the expanded
factor inputs (from the ledger position + live insider aggregates), and a company
snapshot (yfinance). It explains WHY THE ALGORITHM ranked the name — it never
generates an investment thesis or persuasive narrative. The composite has shown no
predictive power (R²≈0.004), so every detail view carries a standing honest-framing
line and recommends nothing.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qivc.dashboard.build import (
    BASELINE_WEIGHTS,
    PriceProvider,
    read_records,
    reconstruct_trades,
)

log = logging.getLogger(__name__)

_PROFILE_CACHE = "data/paper/_profile_cache.json"
_PROFILE_TTL_S = 24 * 60 * 60
_INSIDER_LOOKBACK_DAYS = 90
_OPPORTUNISTIC = "opportunistic"
_FACTORS = ["insider", "quality", "valuation", "momentum", "technical"]
ProfileFetch = Callable[[str], dict[str, Any]]


def _honest_line(entry_date: str) -> str:
    return (
        f"Candidate because it ranked top-decile by composite score on {entry_date}. "
        "The composite has not shown predictive power (R²≈0.004 in backtesting); this "
        "view explains the mechanics of the ranking, not a recommendation."
    )


# ---------------------------------------------------------------------------
# Form 4 insider evidence (from form4_historical; offline) — MIRRORS THE SCORER
#
# The scorer (opportunistic_insider_raw) classifies the buys FILED in the
# *window_days* before the rebalance date, deduped by accession, classified CMP
# as-of that date. The drill-down must reproduce EXACTLY that set/classification —
# not classify the oldest historical txn as-of today (the Task-15.4 bug). So we
# build the same window+classification and label rows from it.
# ---------------------------------------------------------------------------
def _insider_window(
    db_path: str, ticker: str, entry_date: _dt.date, window_days: int
) -> tuple[list[Any], dict[str, str]]:
    """The scorer's exact window txns + per-CIK CMP classification (offline)."""
    from qivc.backtest.pit import filter_by_filing_date
    from qivc.backtest.signal import _classify_window
    from qivc.data.bulk_loader import read_purchases
    from qivc.filters.cluster_detector import dedupe_by_accession

    ws = entry_date - _dt.timedelta(days=window_days)
    txns = read_purchases(db_path, ws, entry_date, ticker=ticker)  # FILED in [ws, entry]
    txns = filter_by_filing_date(txns, entry_date)
    txns = dedupe_by_accession(txns)
    cls: dict[str, str] = {}
    if txns:
        try:
            cls = _classify_window(db_path, txns, entry_date, 3)
        except Exception as exc:  # best-effort; show raw evidence anyway
            log.warning("drill-down: insider classification failed for %s: %s", ticker, exc)
    return txns, cls


def ticker_form4_history(
    db_path: str, ticker: str, *, entry_date: _dt.date, window_days: int = _INSIDER_LOOKBACK_DAYS,
) -> list[dict[str, Any]]:
    """
    Every P-code purchase for *ticker* knowable at *entry_date* (newest first). The
    CMP classification of each row uses the SAME window + as-of date the scorer used
    (so the opportunistic set matches the score); buys outside the window are marked
    'outside window'.
    """
    from qivc.data.bulk_loader import read_purchases

    all_txns = read_purchases(db_path, _dt.date(2006, 1, 1), entry_date, ticker=ticker)
    _win, cls = _insider_window(db_path, ticker, entry_date, window_days)
    ws_iso = (entry_date - _dt.timedelta(days=window_days)).isoformat()
    rows = []
    for t in sorted(all_txns, key=lambda x: x.filed_date, reverse=True):
        role = (
            "Officer" if t.is_officer
            else "Director" if t.is_director
            else "10% owner" if t.is_ten_percent_owner
            else "—"
        )
        in_window = t.filed_date.isoformat() >= ws_iso
        klass = cls.get(t.cik, "unclassified") if in_window else "outside window"
        rows.append({
            "insider": t.name, "role": role, "date": t.transaction_date.isoformat(),
            "filed": t.filed_date.isoformat(), "shares": round(t.shares),
            "value": round(t.value_usd), "classification": klass,
        })
    return rows


def insider_aggregates(
    db_path: str, ticker: str, entry_date: _dt.date, window_days: int = _INSIDER_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Cluster aggregates over the OPPORTUNISTIC buys in the scorer's window (exact)."""
    txns, cls = _insider_window(db_path, ticker, entry_date, window_days)
    opp = [t for t in txns if cls.get(t.cik) == _OPPORTUNISTIC]
    return {
        "lookback_days": window_days,
        "n_in_window": len(txns),
        "n_opportunistic": len(opp),
        "cluster_size": len({t.cik for t in opp}),
        "total_usd": round(sum(t.value_usd for t in opp)),
        "csuite": any(t.is_officer for t in opp),
    }


# ---------------------------------------------------------------------------
# Company profile (yfinance .info; cached, injectable)
# ---------------------------------------------------------------------------
def _yf_profile(ticker: str) -> dict[str, Any]:
    import yfinance as yf

    info = yf.Ticker(ticker).info
    return {
        "company": info.get("shortName") or info.get("longName"),
        "sector": info.get("sector"), "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "summary": info.get("longBusinessSummary"),
        "revenue": info.get("totalRevenue"),
        "profit_margin": info.get("profitMargins"),
    }


def company_profile(
    ticker: str, *, fetch: ProfileFetch | None = None, cache_path: str = _PROFILE_CACHE,
    now: float | None = None,
) -> dict[str, Any]:
    """yfinance company snapshot, cached to disk (TTL 1 day). *fetch* injectable for tests."""
    if fetch is not None:
        return fetch(ticker)
    import time

    p = Path(cache_path)
    blob: dict[str, Any] = {}
    if p.exists() and (now or time.time()) - p.stat().st_mtime < _PROFILE_TTL_S:
        blob = json.loads(p.read_text())
        if ticker in blob:
            return blob[ticker]  # type: ignore[no-any-return]
    try:
        prof = _yf_profile(ticker)
    except Exception as exc:  # profile is non-essential context
        log.warning("drill-down: profile fetch failed for %s: %s", ticker, exc)
        prof = {"company": None, "sector": None, "industry": None}
    blob[ticker] = prof
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(blob))
    return prof


# ---------------------------------------------------------------------------
# Technical indicators (REFERENCE ONLY — 0% weight, shelved per Task 14)
# ---------------------------------------------------------------------------
def technical_indicators(
    ticker: str, entry_date: str, price_provider: PriceProvider | None
) -> dict[str, Any] | None:
    """
    RSI(14), % below the 60-day high, and the blended oversold score AS OF the entry
    date — computed live from prices for REFERENCE only. The technical factor has 0%
    weight (shelved, regime-conditional, failed the 2022 PIT test); these values do
    NOT affect the composite or candidacy. None if no price provider / data.
    """
    if price_provider is None:
        return None
    from qivc.backtest.technical import (
        HIGH_LOOKBACK,
        pct_below_high,
        technical_oversold_score,
        wilder_rsi,
    )

    ed = _dt.date.fromisoformat(entry_date)
    series = price_provider.daily_closes([ticker], ed - _dt.timedelta(days=95), ed)
    raw = series.get(ticker, {})
    closes = [v for d, v in sorted(raw.items()) if d <= entry_date][-(HIGH_LOOKBACK + 5):]
    if not closes:
        return None
    rsi = wilder_rsi(closes)
    pbh = pct_below_high(closes)
    score = technical_oversold_score(closes)
    return {
        "rsi": round(rsi, 1) if rsi is not None else None,
        "pct_below_high": round(pbh * 100, 1) if pbh is not None else None,
        "blended": round(score, 3) if score is not None else None,
    }


# ---------------------------------------------------------------------------
# Assemble the detail view
# ---------------------------------------------------------------------------
def _mechanics(components: dict[str, float]) -> list[dict[str, Any]]:
    """Factual per-factor breakdown: sub-score x weight, phrased as 'what the score is'."""
    out = []
    for i, f in enumerate(_FACTORS):
        w = BASELINE_WEIGHTS[i]
        sub = components.get(f)
        if f == "technical":
            note = "shelved, 0% weight (regime-conditional per Task 14)"
        elif f in ("valuation", "momentum"):
            note = "neutral input (no PIT source); contributes the universe median"
        elif w == max(BASELINE_WEIGHTS):
            note = "the dominant driver of the composite"
        else:
            note = "contributes at its weight"
        out.append({
            "factor": f, "weight": w,
            "sub_score": round(sub, 3) if sub is not None else None, "note": note,
        })
    return out


def build_ticker_detail(
    ledger: str,
    config: str,
    ticker: str,
    *,
    db_path: str,
    today: _dt.date,
    profile_fetch: ProfileFetch | None = None,
    price_provider: PriceProvider | None = None,
    window_days: int = _INSIDER_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Assemble the full factual drill-down for *ticker* (or a not-found shell).

    *window_days* is the config's insider-aggregation window (90 for v3.0, 14 for
    the v3.0-w14 fork) — the drill-down's insider evidence mirrors that window.
    """
    ticker = ticker.upper()
    records = read_records(ledger, config)
    closed, open_ = reconstruct_trades(records)
    span = next((s for s in open_ if s.ticker == ticker), None)
    status = "open"
    if span is None:
        cands = [s for s in closed if s.ticker == ticker]
        span = max(cands, key=lambda s: s.exit_date or "", default=None)
        status = "closed"
    profile = company_profile(ticker, fetch=profile_fetch)
    if span is None:
        return {
            "ticker": ticker, "found": False, "profile": profile,
            "honest_line": (
                "This ticker is not in the paper ledger. The drill-down explains the "
                "mechanics of a ranking, not a recommendation."
            ),
        }

    entry = _dt.date.fromisoformat(span.entry_date)
    history = ticker_form4_history(db_path, ticker, entry_date=entry, window_days=window_days)
    agg = insider_aggregates(db_path, ticker, entry, window_days)
    has_inputs = bool(span.inputs)
    return {
        "ticker": ticker, "found": True, "status": status,
        "honest_line": _honest_line(span.entry_date),
        "header": {
            "company": profile.get("company") or ticker,
            "sector": profile.get("sector"), "industry": profile.get("industry"),
            "current_price": profile.get("current_price"),
            "composite": round(span.composite, 3),
            "rank": "top-decile (entry rule: composite ≥ 90th percentile)",
            "entry_date": span.entry_date,
        },
        "mechanics": _mechanics(span.components),
        "inputs": dict(span.inputs) if has_inputs else None,
        "insider_aggregates": agg,
        "technical": technical_indicators(ticker, span.entry_date, price_provider),
        "form4": history,
        "profile": profile,
    }
