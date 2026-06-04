"""Tests for the ticker drill-down — factual mechanics readout (Task 15.3)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from qivc.dashboard.detail import (
    build_ticker_detail,
    company_profile,
    insider_aggregates,
    ticker_form4_history,
)
from qivc.dashboard.render import render_detail_html
from qivc.storage.db import get_connection

_TODAY = dt.date(2026, 6, 4)
_CONFIG = "N10_thr90_hold30"
_FAKE_PROFILE = {
    "company": "Applied Optoelectronics Inc", "sector": "Technology",
    "industry": "Semiconductors", "market_cap": 1.2e9, "current_price": 26.4,
    "summary": "AAOI designs optical networking products.", "revenue": 2.5e8,
    "profit_margin": -0.05,
}


def _seed_form4(db: str) -> None:
    rows = [
        # cik,name,title,ticker,shares,price,value,txn,filed,P,dir,off,10pct,acc,quarter
        ["111", "Jane Insider", "CEO", "AAOI", 5000, 24.0, 120000.0,
         dt.date(2026, 5, 10), dt.date(2026, 5, 12), "P", False, True, False, "a1", "2026q2"],
        ["222", "Bob Director", "Director", "AAOI", 2000, 23.5, 47000.0,
         dt.date(2026, 5, 9), dt.date(2026, 5, 11), "P", True, False, False, "a2", "2026q2"],
    ]
    with get_connection(db) as conn:
        conn.executemany(
            """INSERT INTO form4_historical (cik,name,title,ticker,shares,price,value_usd,
               transaction_date,filed_date,transaction_code,is_director,is_officer,
               is_ten_percent_owner,accession_number,source_quarter)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()


def _write_ledger(tmp_path: Path, *, with_inputs: bool = True) -> str:
    p = tmp_path / "ledger.jsonl"
    pos = {
        "ticker": "AAOI", "sector": "Technology", "weight": 1.0, "composite": 0.71,
        "entry_date": "2026-05-20",
        "components": {"insider": 0.88, "quality": 0.52, "valuation": 0.5,
                       "momentum": 0.5, "technical": 0.5},
    }
    if with_inputs:
        pos["inputs"] = {"fscore": 6, "gpa": 0.31, "insider_raw": 2.4,
                         "valuation_raw": None, "momentum_raw": None, "technical_raw": None}
    rec = {"as_of": "2026-05-20", "config": _CONFIG, "regime": "risk-on",
           "equity_pct": 100.0, "n_scored": 30, "positions": [pos]}
    p.write_text(json.dumps(rec) + "\n")
    return str(p)


def test_ticker_form4_history_from_store(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    _seed_form4(db)
    hist = ticker_form4_history(db, "AAOI", as_of=_TODAY)
    assert len(hist) == 2
    assert hist[0]["date"] >= hist[1]["date"]            # newest first
    jane = next(h for h in hist if h["insider"] == "Jane Insider")
    assert jane["role"] == "Officer" and jane["value"] == 120000
    assert "classification" in jane                       # CMP class present (offline)


def test_insider_aggregates_window() -> None:
    hist = [
        {"insider": "A", "role": "Officer", "date": "2026-05-10",
         "shares": 1, "value": 100000, "classification": "opportunistic"},
        {"insider": "B", "role": "Director", "date": "2026-05-09",
         "shares": 1, "value": 50000, "classification": "opportunistic"},
        {"insider": "C", "role": "—", "date": "2025-01-01",  # outside 90d window
         "shares": 1, "value": 999, "classification": "opportunistic"},
    ]
    agg = insider_aggregates(hist, "2026-05-20")
    assert agg["cluster_size"] == 2          # A, B (C is out of window)
    assert agg["n_opportunistic"] == 2
    assert agg["total_usd"] == 150000
    assert agg["csuite"] is True
    assert agg["lookback_days"] == 90


def test_company_profile_uses_injected_fetch() -> None:
    prof = company_profile("AAOI", fetch=lambda t: _FAKE_PROFILE)
    assert prof["company"] == "Applied Optoelectronics Inc"


def test_build_detail_with_inputs_and_render(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    _seed_form4(db)
    detail = build_ticker_detail(
        _write_ledger(tmp_path), _CONFIG, "AAOI",
        db_path=db, today=_TODAY, profile_fetch=lambda t: _FAKE_PROFILE,
    )
    assert detail["found"] and detail["status"] == "open"
    assert detail["inputs"] == {"fscore": 6, "gpa": 0.31, "insider_raw": 2.4,
                                "valuation_raw": None, "momentum_raw": None, "technical_raw": None}
    assert len(detail["mechanics"]) == 5
    insider_m = next(m for m in detail["mechanics"] if m["factor"] == "insider")
    assert insider_m["weight"] == 40 and "dominant" in insider_m["note"]
    tech_m = next(m for m in detail["mechanics"] if m["factor"] == "technical")
    assert "shelved" in tech_m["note"]
    assert len(detail["form4"]) == 2

    html = render_detail_html(detail)
    assert "not a recommendation" in html              # honest-framing line present
    assert "Form 4 purchases" in html and "AAOI" in html
    assert "F-score 6" in html                          # recorded quality input shown
    # NO persuasive / investment-thesis language
    low = html.lower()
    for pitch in ("compelling", "undervalued", "strong buy", "we recommend",
                  "bullish", "conviction", "thesis", "price target"):
        assert pitch not in low


def test_build_detail_degrades_without_recorded_inputs(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    _seed_form4(db)
    detail = build_ticker_detail(
        _write_ledger(tmp_path, with_inputs=False), _CONFIG, "AAOI",
        db_path=db, today=_TODAY, profile_fetch=lambda t: _FAKE_PROFILE,
    )
    assert detail["inputs"] is None
    html = render_detail_html(detail)
    assert "input detail not recorded for this entry" in html


class _FakePrices:
    """Falling series so RSI is low and % below high is positive (deterministic)."""

    def daily_closes(self, tickers, start, end):  # type: ignore[no-untyped-def]
        import datetime as _dt

        out = {}
        for t in tickers:
            days = [(start + _dt.timedelta(days=i)) for i in range((end - start).days + 1)]
            out[t] = {d.isoformat(): float(120 - i) for i, d in enumerate(days)}
        return out


def test_technical_indicators_reference_values(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    _seed_form4(db)
    detail = build_ticker_detail(
        _write_ledger(tmp_path), _CONFIG, "AAOI", db_path=db, today=_TODAY,
        profile_fetch=lambda t: _FAKE_PROFILE, price_provider=_FakePrices(),
    )
    tech = detail["technical"]
    assert tech is not None
    assert tech["rsi"] == 0.0                      # monotonic decline -> RSI 0
    assert tech["pct_below_high"] is not None and tech["pct_below_high"] > 0
    assert tech["blended"] is not None

    html = render_detail_html(detail)
    # the technical values are shown...
    assert "RSI(14)" in html and "below 60-day high" in html
    # ...with the prominent shelved / 0% / reference-only flag
    assert "0% weight · SHELVED" in html
    assert "failed the 2022 PIT regime test" in html
    assert "does NOT contribute to the composite or candidacy" in html


def test_technical_absent_without_price_provider(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    _seed_form4(db)
    detail = build_ticker_detail(
        _write_ledger(tmp_path), _CONFIG, "AAOI", db_path=db, today=_TODAY,
        profile_fetch=lambda t: _FAKE_PROFILE,  # no price_provider
    )
    assert detail["technical"] is None
    html = render_detail_html(detail)
    # the shelved flag is still shown even when values are unavailable
    assert "0% weight · SHELVED" in html
    assert "values not available" in html


def test_detail_not_in_ledger(tmp_path: Path) -> None:
    db = str(tmp_path / "q.db")
    _seed_form4(db)
    detail = build_ticker_detail(
        _write_ledger(tmp_path), _CONFIG, "ZZZZ",
        db_path=db, today=_TODAY, profile_fetch=lambda t: {"sector": None, "industry": None},
    )
    assert detail["found"] is False
    html = render_detail_html(detail)
    assert "not in the paper ledger" in html.lower()
    assert "not a recommendation" in html.lower()       # framing still present
