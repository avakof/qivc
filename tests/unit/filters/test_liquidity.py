"""Unit tests for the liquidity filter."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from qivc.filters.liquidity import _trading_days_between, apply
from qivc.schemas import Liquidity
from tests.unit.filters.test_fscore import _make_candidate, _make_fundamentals

_TARGET_POS = 1_000_000.0  # $1M position → ADV must be ≥ $20M


def _candidate_with_liquidity(
    market_cap: float = 500_000_000.0,
    adv: float = 50_000_000.0,
    next_earnings: date | None = None,
    has_ma: bool = False,
) -> object:
    f = _make_fundamentals()
    c = _make_candidate(f)
    liq = Liquidity(
        ticker="TEST",
        market_cap_usd=market_cap,
        adv_20d_usd=adv,
        next_earnings_date=next_earnings,
        has_pending_ma=has_ma,
    )
    return c.model_copy(update={"liquidity": liq})


# ---------------------------------------------------------------------------
# Pass case
# ---------------------------------------------------------------------------


def test_all_gates_pass() -> None:
    c = _candidate_with_liquidity(
        market_cap=500_000_000.0,
        adv=50_000_000.0,
        next_earnings=None,
    )
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is True
    assert result.filter_name == "liquidity"


# ---------------------------------------------------------------------------
# Fail — market cap
# ---------------------------------------------------------------------------


def test_fail_market_cap_below_300m() -> None:
    c = _candidate_with_liquidity(market_cap=200_000_000.0)
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is False
    assert result.metric_value == pytest.approx(200_000_000.0)
    assert result.threshold == pytest.approx(300_000_000.0)


def test_pass_market_cap_exactly_300m() -> None:
    c = _candidate_with_liquidity(market_cap=300_000_000.0)
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is True


# ---------------------------------------------------------------------------
# Fail — ADV
# ---------------------------------------------------------------------------


def test_fail_adv_below_20x_position() -> None:
    c = _candidate_with_liquidity(adv=15_000_000.0)  # < 20 x $1M = $20M
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is False
    assert result.threshold == pytest.approx(20_000_000.0)


def test_pass_adv_exactly_20x() -> None:
    c = _candidate_with_liquidity(adv=20_000_000.0)
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is True


# ---------------------------------------------------------------------------
# Fail — earnings proximity
# ---------------------------------------------------------------------------


def test_fail_earnings_within_5_trading_days() -> None:
    """Earnings in 3 trading days (Mon-Fri) → FAIL."""
    today = date.today()
    # Find next 3 trading days from today
    count = 0
    d = today
    while count < 3:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    c = _candidate_with_liquidity(next_earnings=d)
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is False


def test_pass_earnings_well_in_future() -> None:
    future = date.today() + timedelta(days=60)
    c = _candidate_with_liquidity(next_earnings=future)
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is True


def test_pass_no_earnings_date() -> None:
    c = _candidate_with_liquidity(next_earnings=None)
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is True


# ---------------------------------------------------------------------------
# Fail — pending M&A
# ---------------------------------------------------------------------------


def test_fail_pending_ma() -> None:
    c = _candidate_with_liquidity(has_ma=True)
    result = apply(c, _TARGET_POS)  # type: ignore[arg-type]
    assert result.passed is False
    assert "M&A" in result.reason


# ---------------------------------------------------------------------------
# _trading_days_between helper
# ---------------------------------------------------------------------------


def test_trading_days_same_date_is_zero() -> None:
    d = date(2026, 5, 1)
    assert _trading_days_between(d, d) == 0


def test_trading_days_weekend_skipped() -> None:
    # 2026-05-01 is a Friday; next Mon is 2026-05-04 → 1 trading day
    assert _trading_days_between(date(2026, 5, 1), date(2026, 5, 4)) == 1


def test_trading_days_one_week() -> None:
    # Mon to next Mon = 5 trading days
    assert _trading_days_between(date(2026, 5, 4), date(2026, 5, 11)) == 5
