"""Tests for the live forward paper-trade dashboard (Task 15)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

from qivc.dashboard import (
    build_dashboard_data,
    render_html,
    sample_size_label,
    top3_concentration,
)

_TODAY = dt.date(2026, 6, 4)
_CONFIG = "N10_thr90_hold30"


def _factors(insider: float) -> dict[str, float]:
    return {"insider": insider, "quality": 0.4, "valuation": 0.5,
            "momentum": 0.45, "technical": 0.5}


def _write_ledger(tmp_path: Path) -> str:
    """Two snapshots: AAA closes 05-20; BBB + CCC open in the last snapshot."""
    p = tmp_path / "ledger.jsonl"
    recs = [
        {"as_of": "2026-04-20", "config": _CONFIG, "regime": "risk-on",
         "equity_pct": 100.0, "n_scored": 30, "positions": [
            {"ticker": "AAA", "sector": "Energy", "weight": 0.5, "composite": 0.70,
             "entry_date": "2026-04-20", "components": _factors(0.82)},
            {"ticker": "BBB", "sector": "Health Care", "weight": 0.5, "composite": 0.68,
             "entry_date": "2026-04-20", "components": _factors(0.80)}]},
        {"as_of": "2026-05-20", "config": _CONFIG, "regime": "risk-on",
         "equity_pct": 100.0, "n_scored": 28, "positions": [
            {"ticker": "BBB", "sector": "Health Care", "weight": 0.5, "composite": 0.68,
             "entry_date": "2026-04-20", "components": _factors(0.80)},
            {"ticker": "CCC", "sector": "Energy", "weight": 0.5, "composite": 0.66,
             "entry_date": "2026-05-20", "components": _factors(0.78)}]},
    ]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return str(p)


_PRICE_DATA = {
    "AAA": {"2026-04-20": 10.0, "2026-05-20": 12.0},          # +20% closed
    "BBB": {"2026-04-20": 20.0, "2026-05-20": 21.0, "2026-06-03": 22.0},  # open +10%
    "CCC": {"2026-05-20": 5.0, "2026-06-03": 4.5},            # open -10% (pre-delist)
    "IWN": {"2026-04-20": 100.0, "2026-04-27": 101.0, "2026-05-04": 99.0,
            "2026-05-11": 102.0, "2026-05-18": 103.0, "2026-05-20": 103.0,
            "2026-05-27": 104.0, "2026-06-03": 105.0},
    "^IRX": {"2026-04-20": 5.0, "2026-06-03": 5.0},
}


class FakePrices:
    def daily_closes(self, tickers, start, end):  # type: ignore[no-untyped-def]
        return {t: dict(_PRICE_DATA.get(t, {})) for t in tickers}


# ---------------------------------------------------------------------------
# pure metric helpers
# ---------------------------------------------------------------------------
def test_sample_size_label_thresholds() -> None:
    assert sample_size_label(0) == ("void", "void")
    assert sample_size_label(9) == ("void", "void")
    assert sample_size_label(10) == ("thin", "thin")
    assert sample_size_label(29) == ("thin", "thin")
    assert sample_size_label(30) == ("meaningful", "ok")
    assert sample_size_label(100) == ("meaningful", "ok")


def test_top3_concentration() -> None:
    import pytest

    assert top3_concentration([20, 10, 5]) == 100.0           # all from top 3
    assert top3_concentration([20, 10, 8, 5, -3]) == pytest.approx(38 / 43 * 100)
    assert top3_concentration([-1, -2]) == 0.0                # no winners
    assert top3_concentration([]) == 0.0


# ---------------------------------------------------------------------------
# build_dashboard_data
# ---------------------------------------------------------------------------
def test_headline_both_modes(tmp_path: Path) -> None:
    data = build_dashboard_data(
        _write_ledger(tmp_path), _CONFIG, price_provider=FakePrices(), today=_TODAY
    )
    h = data["headline"]
    assert h["n"] == 1                       # only AAA closed
    assert h["nLabel"] == "void" and h["nChip"] == "void"
    # Mode B: realized-on-close = weight x return = 0.5 * 20% = 10.0%
    assert h["B"]["ret"] == 10.0
    assert "realized on close" in h["B"]["sub"]
    # Mode A: includes open marks via the equity endpoint (finite, labelled)
    assert isinstance(h["A"]["ret"], float)
    assert "marked-to-market" in h["A"]["sub"]
    # concentration: single winner -> 100% of positive P&L
    assert h["conc"] == 100


def test_open_positions_live_marked(tmp_path: Path) -> None:
    data = build_dashboard_data(
        _write_ledger(tmp_path), _CONFIG, price_provider=FakePrices(), today=_TODAY
    )
    opens = {o["ticker"]: o for o in data["open"]}
    assert set(opens) == {"BBB", "CCC"}
    assert opens["BBB"]["mark"] == 22.0
    assert opens["BBB"]["ret"] == 10.0       # (22/20 - 1) * 100, live-marked
    assert opens["BBB"]["holdTgt"] == 30
    # factor micro-bar vector has all 5 sub-scores
    assert len(opens["BBB"]["f"]) == 5


def test_delisting_row_uses_b3_rules(tmp_path: Path) -> None:
    # CCC delists (bankruptcy) mid-hold -> conservative B3 mark = last_px * 0.10.
    def delisting_lookup(ticker: str):  # type: ignore[no-untyped-def]
        if ticker == "CCC":
            return SimpleNamespace(filed_date=dt.date(2026, 5, 25), reason="bankruptcy")
        return None

    data = build_dashboard_data(
        _write_ledger(tmp_path), _CONFIG, price_provider=FakePrices(),
        today=_TODAY, delisting_lookup=delisting_lookup,
    )
    ccc = next(o for o in data["open"] if o["ticker"] == "CCC")
    assert ccc["delisted"] == "bankruptcy"
    assert ccc["mark"] == 0.45               # 4.5 (last close) * 0.10  (-90%)
    assert ccc["ret"] == -91.0               # (0.45/5 - 1) * 100


def test_equity_dates_are_forward_only(tmp_path: Path) -> None:
    data = build_dashboard_data(
        _write_ledger(tmp_path), _CONFIG, price_provider=FakePrices(), today=_TODAY
    )
    assert data["equity"], "expected a non-empty forward equity series"
    for pt in data["equity"]:
        assert pt["iso"] >= "2026-04-20"     # never before the ledger's first entry
        assert pt["iso"] <= _TODAY.isoformat()


def _write_records(tmp_path: Path, recs: list[dict]) -> str:
    p = tmp_path / "l.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return str(p)


def _cash_record(as_of: str, n_scored: int) -> dict:
    return {"as_of": as_of, "config": _CONFIG, "regime": "risk-on",
            "equity_pct": 0.0, "n_scored": n_scored, "positions": []}


def test_empty_state_zero_candidates_tight_window(tmp_path: Path) -> None:
    """W14-style: ledger HAS a rebalance that scored 0 -> tight-window cash message.

    The message is computed server-side (meta.emptyMessage), so it is the ONLY
    empty-state text in the output and is distinguishable from the new-ledger case."""
    led = _write_records(tmp_path, [_cash_record("2026-06-04", 0)])
    data = build_dashboard_data(led, _CONFIG, price_provider=FakePrices(),
                                today=_TODAY, window=14)
    assert data["meta"]["latest"]["n_scored"] == 0 and data["meta"]["window"] == 14
    msg = data["meta"]["emptyMessage"]
    assert "0 candidates this rebalance" in msg
    assert "14-day window" in msg
    assert "100% cash" in msg and "tight-window tradeoff" in msg
    assert "accumulating forward data" not in msg
    # the selected message (ASCII chunk) reaches the page; only this branch's text
    # is embedded, so it's distinguishable from the new-ledger message
    assert "tight-window tradeoff" in render_html(data)


def test_empty_state_scored_but_none_cleared(tmp_path: Path) -> None:
    """Scored >0 but held 0 (threshold) -> distinct message, not the tight-window one."""
    led = _write_records(tmp_path, [_cash_record("2026-06-04", 18)])
    msg = build_dashboard_data(led, _CONFIG, price_provider=FakePrices(),
                               today=_TODAY)["meta"]["emptyMessage"]
    assert "0 held this rebalance" in msg
    assert "scored 18 insider-active" in msg
    assert "none cleared the entry threshold" in msg
    assert "tight-window tradeoff" not in msg


def test_empty_state_new_ledger_message(tmp_path: Path) -> None:
    data = build_dashboard_data(str(tmp_path / "none.jsonl"), _CONFIG,
                                price_provider=FakePrices(), today=_TODAY)
    assert data["meta"]["latest"] is None
    msg = data["meta"]["emptyMessage"]
    assert "accumulating forward data" in msg
    assert "0 candidates this rebalance" not in msg


def test_empty_ledger_renders_without_error(tmp_path: Path) -> None:
    data = build_dashboard_data(
        str(tmp_path / "nope.jsonl"), _CONFIG, price_provider=FakePrices(), today=_TODAY
    )
    assert data["headline"]["n"] == 0
    assert data["closed"] == [] and data["open"] == [] and data["equity"] == []
    html = render_html(data)
    assert "<!DOCTYPE html>" in html
    assert "not validated" in html.lower()           # permanent banner
    assert "accumulating forward data" in html        # graceful empty state


def test_render_contains_no_backtest_data_and_banner(tmp_path: Path) -> None:
    data = build_dashboard_data(
        _write_ledger(tmp_path), _CONFIG, price_provider=FakePrices(), today=_TODAY
    )
    html = render_html(data)
    assert "PAPER_DATA" in html and "AAA" in html
    # default headline mode is B (realized-only button carries the 'on' class)
    assert '<button data-mode="B" class="on">' in html
    assert "renderHeadline('B')" in html
