"""
Unit tests for the CMP insider classifier.
All three mandatory test cases from the spec are explicitly present.
"""

from __future__ import annotations

from datetime import date

import pytest

from qivc.filters.insider_classifier import classify
from qivc.schemas import InsiderHistory, InsiderTransaction

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _txn(cik: str, d: date, ticker: str = "FCN") -> InsiderTransaction:
    return InsiderTransaction(
        cik=cik,
        name="Test Insider",
        title="CFO",
        ticker=ticker,
        shares=1000.0,
        price=50.0,
        value_usd=50_000.0,
        transaction_date=d,
        filed_date=d,
        transaction_code="P",
        is_director=False,
        is_officer=True,
        is_ten_percent_owner=False,
    )


def _history(cik: str, txns: list[InsiderTransaction], years: int) -> InsiderHistory:
    return InsiderHistory(cik=cik, transactions=txns, years_of_history=years)


# ---------------------------------------------------------------------------
# MANDATORY test cases (from spec)
# ---------------------------------------------------------------------------


def test_mandatory_routine_may_three_consecutive_years() -> None:
    """
    Insider with trades in May 2023, May 2024, May 2025 →
    new May 2026 trade MUST be classified as ROUTINE.
    """
    cik = "11111"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    prior_trades = [
        _txn(cik, date(2023, 5, 10)),
        _txn(cik, date(2024, 5, 8)),
        _txn(cik, date(2025, 5, 12)),
    ]
    history = _history(cik, prior_trades, years=3)

    result = classify(candidate_txn, history)
    assert result == "routine", f"Expected 'routine', got '{result}'"


def test_mandatory_opportunistic_gap_in_2024() -> None:
    """
    Insider with trades in May 2023, May 2025 (gap in 2024) →
    new May 2026 trade MUST be classified as OPPORTUNISTIC.
    """
    cik = "22222"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    prior_trades = [
        _txn(cik, date(2023, 5, 10)),
        # No May 2024 trade
        _txn(cik, date(2025, 5, 12)),
    ]
    history = _history(cik, prior_trades, years=3)

    result = classify(candidate_txn, history)
    assert result == "opportunistic", f"Expected 'opportunistic', got '{result}'"


def test_mandatory_unclassified_one_year_history() -> None:
    """
    Insider with only 1 year of history →
    new trade MUST be classified as UNCLASSIFIED.
    """
    cik = "33333"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    prior_trades = [_txn(cik, date(2025, 5, 10))]
    history = _history(cik, prior_trades, years=1)

    result = classify(candidate_txn, history)
    assert result == "unclassified", f"Expected 'unclassified', got '{result}'"


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


def test_exactly_three_years_history_opportunistic() -> None:
    """3 years history but different months → opportunistic."""
    cik = "44444"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    prior_trades = [
        _txn(cik, date(2023, 3, 10)),  # March, not May
        _txn(cik, date(2024, 5, 8)),  # May 2024 ✓
        _txn(cik, date(2025, 5, 12)),  # May 2025 ✓
    ]
    history = _history(cik, prior_trades, years=3)
    # Missing May 2023 → opportunistic
    assert classify(candidate_txn, history) == "opportunistic"


def test_two_years_history_is_unclassified() -> None:
    cik = "55555"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    prior_trades = [
        _txn(cik, date(2024, 5, 8)),
        _txn(cik, date(2025, 5, 12)),
    ]
    history = _history(cik, prior_trades, years=2)
    assert classify(candidate_txn, history) == "unclassified"


def test_extra_same_month_trades_still_routine() -> None:
    """Multiple trades in the same month in required years → still routine."""
    cik = "66666"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    prior_trades = [
        _txn(cik, date(2023, 5, 2)),
        _txn(cik, date(2023, 5, 15)),  # two May 2023 trades
        _txn(cik, date(2024, 5, 8)),
        _txn(cik, date(2025, 5, 12)),
    ]
    history = _history(cik, prior_trades, years=3)
    assert classify(candidate_txn, history) == "routine"


def test_same_month_wrong_year_not_counted() -> None:
    """Trades in year -4 don't count for the routine check."""
    cik = "77777"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    prior_trades = [
        _txn(cik, date(2022, 5, 10)),  # year -4: NOT in {2023,2024,2025}
        _txn(cik, date(2024, 5, 8)),  # year -2 ✓
        _txn(cik, date(2025, 5, 12)),  # year -1 ✓
    ]
    history = _history(cik, prior_trades, years=3)
    # Missing year -3 (2023) → opportunistic
    assert classify(candidate_txn, history) == "opportunistic"


def test_empty_history_with_sufficient_years_opportunistic() -> None:
    """History years >= 3 but no trades in the relevant months → opportunistic."""
    cik = "88888"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    history = _history(cik, [], years=3)  # no transactions at all
    assert classify(candidate_txn, history) == "opportunistic"


@pytest.mark.parametrize("years", [0, 1, 2])
def test_insufficient_years_is_unclassified(years: int) -> None:
    cik = "99999"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    history = _history(cik, [], years=years)
    assert classify(candidate_txn, history) == "unclassified"


def test_insider_with_six_months_history_is_unclassified() -> None:
    """
    An insider with only ~6 months of EDGAR history (years_of_history=0 after the
    OQ-2 fix) must be UNCLASSIFIED, not opportunistic — the safety fallback.
    """
    cik = "60606"
    candidate_txn = _txn(cik, date(2026, 5, 15))
    # Even with a recent prior trade, <3 years of measured history → unclassified.
    prior = [_txn(cik, date(2026, 1, 10))]
    history = _history(cik, prior, years=0)
    assert classify(candidate_txn, history) == "unclassified"
