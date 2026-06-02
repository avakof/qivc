"""Unit tests for the v3.0 composite scorer (Phase 1)."""

from __future__ import annotations

from qivc.backtest.composite_scorer import (
    COMPOSITE_WEIGHTS,
    StockFactors,
    _percentile_ranks,
    score_universe,
)


def test_weights_sum_to_one() -> None:
    assert abs(sum(COMPOSITE_WEIGHTS.values()) - 1.0) < 1e-9


def test_percentile_ranks_ties_and_none() -> None:
    # [10, 20, 20, 30] -> ranks; None -> neutral 0.5
    r = _percentile_ranks([10.0, 20.0, 20.0, 30.0, None])
    # mid-rank: for 10 -> (0 strictly-less + 0.5*1 equal)/4 = 0.125
    assert abs(r[0] - 0.125) < 1e-9
    assert abs(r[1] - r[2]) < 1e-9  # ties share rank
    assert r[4] == 0.5  # None neutral


def test_percentile_ranks_all_none_neutral() -> None:
    assert _percentile_ranks([None, None]) == [0.5, 0.5]


def test_quality_blends_fscore_and_sector_gpa() -> None:
    # Two industrials: one strong (F9, high GP/A), one weak (F2, low GP/A).
    factors = [
        StockFactors(
            "STRONG",
            "Industrials",
            insider_raw=0,
            fscore=9,
            gpa=0.5,
            valuation_raw=None,
            momentum_raw=None,
        ),
        StockFactors(
            "WEAK",
            "Industrials",
            insider_raw=0,
            fscore=2,
            gpa=0.05,
            valuation_raw=None,
            momentum_raw=None,
        ),
    ]
    scored = {s.ticker: s for s in score_universe(factors)}
    assert scored["STRONG"].components["quality"] > scored["WEAK"].components["quality"]


def test_insider_dominates_ranking() -> None:
    # Same quality; the name with insider activity must rank higher (40% weight).
    factors = [
        StockFactors(
            "INS",
            "Industrials",
            insider_raw=5.0,
            fscore=6,
            gpa=0.3,
            valuation_raw=None,
            momentum_raw=None,
        ),
        StockFactors(
            "NOINS",
            "Industrials",
            insider_raw=0.0,
            fscore=6,
            gpa=0.3,
            valuation_raw=None,
            momentum_raw=None,
        ),
    ]
    ranked = score_universe(factors)
    assert ranked[0].ticker == "INS"
    assert ranked[0].composite > ranked[1].composite


def test_missing_valuation_momentum_are_neutral_not_eliminating() -> None:
    # A strong-insider biotech with no GP/A and no valuation/momentum still scores
    # (no hard rejection — the v3.0 fix for v2.x's 0-trade problem).
    factors = [
        StockFactors(
            "BIO",
            "Health Care",
            insider_raw=3.0,
            fscore=1,
            gpa=None,
            valuation_raw=None,
            momentum_raw=None,
        ),
        StockFactors(
            "OTHER",
            "Industrials",
            insider_raw=0.0,
            fscore=8,
            gpa=0.4,
            valuation_raw=None,
            momentum_raw=None,
        ),
    ]
    ranked = score_universe(factors)
    # BIO is not eliminated; with 40% insider weight it can still rank #1.
    assert ranked[0].ticker == "BIO"
    assert all(0.0 <= s.composite <= 1.0 for s in ranked)


def test_empty_universe() -> None:
    assert score_universe([]) == []
