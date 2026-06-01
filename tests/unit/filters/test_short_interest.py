"""Unit tests for the short interest filter — all 4 cases."""

from __future__ import annotations

from datetime import date

import pytest

from qivc.filters.short_interest import apply
from qivc.schemas import InsiderTransaction, ShortInterest
from tests.unit.filters.test_fscore import _make_candidate, _make_fundamentals


def _candidate_with_si(
    si: float, direction: str, transactions: list[InsiderTransaction] | None = None
) -> object:
    f = _make_fundamentals()
    c = _make_candidate(f)
    si_obj = ShortInterest(
        ticker="TEST",
        si_pct_float=si,
        prior_si_pct_float=si,
        report_date=date(2026, 5, 1),
        direction=direction,  # type: ignore[arg-type]
    )
    updates: dict[str, object] = {"short_interest": si_obj}
    if transactions is not None:
        updates["transactions"] = transactions
    return c.model_copy(update=updates)


def _make_txn(
    cik: str = "T1", is_officer: bool = True, value_usd: float = 50_000.0
) -> InsiderTransaction:
    return InsiderTransaction(
        cik=cik,
        name="Insider",
        title="CEO",
        ticker="TEST",
        shares=1000.0,
        price=value_usd / 1000.0,
        value_usd=value_usd,
        transaction_date=date(2026, 5, 1),
        filed_date=date(2026, 5, 1),
        transaction_code="P",
        is_director=False,
        is_officer=is_officer,
        is_ten_percent_owner=False,
    )


# ---------------------------------------------------------------------------
# Case 1 — low SI (< 5%)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", ["falling", "flat", "rising"])
def test_case1_low_si_always_passes(direction: str) -> None:
    c = _candidate_with_si(si=0.03, direction=direction)
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True


def test_case1_si_exactly_5pct_boundary() -> None:
    """SI exactly 5% is NOT in the < 5% bucket."""
    c = _candidate_with_si(si=0.05, direction="falling")
    result = apply(c)  # type: ignore[arg-type]
    # Should hit case 2 (falling in 5-15% band)
    assert result.passed is True
    assert "5-15%" in result.reason or "falling" in result.reason


# ---------------------------------------------------------------------------
# Case 2 — medium SI (5-15%), falling → conviction upgrade
# ---------------------------------------------------------------------------


def test_case2_medium_si_falling() -> None:
    c = _candidate_with_si(si=0.10, direction="falling")
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True
    assert "conviction upgrade" in result.reason


def test_medium_si_rising_neutral_pass() -> None:
    """Medium SI rising: not case 2, should still pass (neutral)."""
    c = _candidate_with_si(si=0.10, direction="rising")
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True


# ---------------------------------------------------------------------------
# Case 3 — high SI (> 15%), rising → conditional
# ---------------------------------------------------------------------------


def test_case3_high_si_rising_no_extra_signals_fails() -> None:
    """High SI + rising + weak fundamentals (F-Score < 8, no big buy) → FAIL."""
    f = _make_fundamentals(
        # Only 5/9 pass → F-Score = 5 < 8
        delta_gross_margin=-0.01,
        delta_asset_turnover=-0.02,
        delta_leverage=0.01,  # bad (increased)
        delta_liquidity=-0.1,  # bad (decreased)
        no_share_issuance=False,  # shares issued
        # GP/A = 0.10 / 3.0B ≈ 0.033 < 0.40 threshold
        gross_profit=100_000_000.0,
        total_assets=3_000_000_000.0,
    )
    c = _make_candidate(f)
    si_obj = ShortInterest(
        ticker="TEST",
        si_pct_float=0.20,
        prior_si_pct_float=0.20,
        report_date=date(2026, 5, 1),
        direction="rising",
    )
    c = c.model_copy(update={"short_interest": si_obj, "transactions": []})
    result = apply(c)
    assert result.passed is False
    assert "BLOCKED" in result.reason


def test_case3_high_si_rising_ceo_buy_passes() -> None:
    """CEO buy ≥ $1M provides extra strength → should pass."""
    txn = _make_txn(is_officer=True, value_usd=1_500_000.0)
    c = _candidate_with_si(si=0.20, direction="rising", transactions=[txn])
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True
    assert "extra strength" in result.reason


def test_case3_high_si_rising_high_fscore_passes() -> None:
    """F-Score ≥ 8 provides extra strength."""
    f = _make_fundamentals()  # all 9 pass → F-Score = 9
    from tests.unit.filters.test_fscore import _make_candidate

    c = _make_candidate(f)
    from qivc.schemas import ShortInterest

    si_obj = ShortInterest(
        ticker="TEST",
        si_pct_float=0.20,
        prior_si_pct_float=0.20,
        report_date=date(2026, 5, 1),
        direction="rising",
    )
    c = c.model_copy(update={"short_interest": si_obj, "transactions": []})
    result = apply(c)
    assert result.passed is True  # F-Score = 9 ≥ 8


# ---------------------------------------------------------------------------
# Case 4 — high SI (> 15%), falling → conviction upgrade
# ---------------------------------------------------------------------------


def test_case4_high_si_falling_passes() -> None:
    c = _candidate_with_si(si=0.20, direction="falling")
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True
    assert "conviction upgrade" in result.reason


def test_case4_high_si_flat_passes() -> None:
    """High SI, flat is handled by case 4 (not rising)."""
    c = _candidate_with_si(si=0.20, direction="flat")
    result = apply(c)  # type: ignore[arg-type]
    assert result.passed is True


def test_case3_high_si_rising_high_gpa_passes() -> None:
    """GP/A ≥ 0.40 provides extra strength → PASS even with rising high SI."""
    f = _make_fundamentals(
        # F-Score < 8 so GP/A must carry the test
        delta_gross_margin=-0.01,
        delta_asset_turnover=-0.02,
        delta_leverage=0.01,
        delta_liquidity=-0.1,
        no_share_issuance=False,
        # GP/A = 1.2B / 3.0B = 0.40 (exactly at threshold)
        gross_profit=1_200_000_000.0,
        total_assets=3_000_000_000.0,
    )
    c = _make_candidate(f)
    si_obj = ShortInterest(
        ticker="TEST",
        si_pct_float=0.20,
        prior_si_pct_float=0.20,
        report_date=date(2026, 5, 1),
        direction="rising",
    )
    c = c.model_copy(update={"short_interest": si_obj, "transactions": []})
    result = apply(c)
    assert result.passed is True  # GP/A = 0.40 ≥ threshold


# ---------------------------------------------------------------------------
# Filter metadata
# ---------------------------------------------------------------------------


def test_filter_name() -> None:
    c = _candidate_with_si(si=0.03, direction="flat")
    assert apply(c).filter_name == "short_interest"  # type: ignore[arg-type]
