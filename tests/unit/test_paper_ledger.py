"""Unit tests for the forward paper-trading ledger (pure logic, no network)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from qivc.backtest.portfolio_constructor import GridConfig, Position
from qivc.paper import (
    PaperRecord,
    append_record,
    build_record,
    parse_config,
    read_last_record,
)
from qivc.schemas import MarketRegime

_REGIME = MarketRegime(
    vix_60d_sma=18.0,
    credit_spread_bps=150.0,
    yield_curve_bps=30.0,
    value_growth_12m=0.02,
    regime="risk-on",
)


def test_parse_config_roundtrips_label() -> None:
    cfg = parse_config("N10_thr90_hold30")
    assert cfg == GridConfig(10, 90.0, 30)
    assert cfg.label == "N10_thr90_hold30"


@pytest.mark.parametrize("bad", ["", "N10_thr90", "garbage", "N10thr90hold30"])
def test_parse_config_rejects_bad_labels(bad: str) -> None:
    with pytest.raises(ValueError, match="bad config"):
        parse_config(bad)


def test_build_record_computes_equity_and_sorts_positions() -> None:
    positions = [
        Position("AAA", "Energy", 0.3, date(2026, 6, 2), 0.61),
        Position("BBB", "Health Care", 0.4, date(2026, 5, 1), 0.72),
    ]
    rec = build_record(date(2026, 6, 2), "N10_thr90_hold30", _REGIME, positions, n_scored=5)
    assert rec.as_of == "2026-06-02"
    assert rec.regime == "risk-on"
    assert rec.equity_pct == 70.0  # 0.3 + 0.4 invested
    assert rec.n_scored == 5
    # sorted by weight desc -> BBB first
    assert [p.ticker for p in rec.positions] == ["BBB", "AAA"]
    assert rec.positions[0].entry_date == "2026-05-01"
    assert "RESEARCH ONLY" in rec.disclaimer


def test_build_record_empty_book_is_all_cash() -> None:
    rec = build_record(date(2026, 6, 2), "N5_thr60_hold90", _REGIME, [], n_scored=0)
    assert rec.equity_pct == 0.0
    assert rec.positions == []


def test_append_and_read_last_record_filters_by_config(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    assert read_last_record(ledger, "N10_thr90_hold30") is None  # missing file

    pos = [Position("AAA", "Energy", 1.0, date(2026, 6, 2), 0.7)]
    append_record(ledger, build_record(date(2026, 6, 2), "N10_thr90_hold30", _REGIME, pos, 3))
    append_record(ledger, build_record(date(2026, 6, 2), "N5_thr60_hold90", _REGIME, [], 0))
    pos2 = [Position("BBB", "Health Care", 1.0, date(2026, 7, 1), 0.8)]
    append_record(ledger, build_record(date(2026, 7, 1), "N10_thr90_hold30", _REGIME, pos2, 4))

    last = read_last_record(ledger, "N10_thr90_hold30")
    assert isinstance(last, PaperRecord)
    assert last.as_of == "2026-07-01"  # most recent for THIS config
    assert [p.ticker for p in last.positions] == ["BBB"]
    # the other config is read independently
    other = read_last_record(ledger, "N5_thr60_hold90")
    assert other is not None and other.positions == []
    # three lines total written
    assert len(ledger.read_text().splitlines()) == 3
