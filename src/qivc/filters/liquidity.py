"""Liquidity filter — pure function."""

from __future__ import annotations

from datetime import date, timedelta

from qivc.schemas import CandidateInput, FilterResult

_FILTER_NAME = "liquidity"
_MIN_MARKET_CAP_USD: float = 300_000_000.0  # $300M
_ADV_MULTIPLIER: int = 20  # ADV must be ≥ 20x position size
_MIN_EARNINGS_TRADING_DAYS: int = 5  # must be > 5 trading days away


def _trading_days_between(start: date, end: date) -> int:
    """Count Mon-Fri trading days strictly between start and end (exclusive start)."""
    if end <= start:
        return 0
    count = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:  # Monday=0 … Friday=4
            count += 1
        current += timedelta(days=1)
    return count


def apply(c: CandidateInput, target_position_usd: float) -> FilterResult:
    """
    Pass if ALL four conditions hold:
    1. market_cap_usd >= $300M
    2. adv_20d_usd >= 20 x target_position_usd
    3. next_earnings_date is None OR > 5 trading days away from today
    4. has_pending_ma is False
    """
    liq = c.liquidity
    today = date.today()

    # Gate 1 — market cap
    if liq.market_cap_usd < _MIN_MARKET_CAP_USD:
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=False,
            metric_value=liq.market_cap_usd,
            threshold=_MIN_MARKET_CAP_USD,
            reason=(f"Market cap ${liq.market_cap_usd:,.0f} < minimum ${_MIN_MARKET_CAP_USD:,.0f}"),
        )

    # Gate 2 — average daily volume
    adv_threshold = target_position_usd * _ADV_MULTIPLIER
    if liq.adv_20d_usd < adv_threshold:
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=False,
            metric_value=liq.adv_20d_usd,
            threshold=adv_threshold,
            reason=(
                f"ADV ${liq.adv_20d_usd:,.0f} < {_ADV_MULTIPLIER}x position "
                f"${target_position_usd:,.0f} = ${adv_threshold:,.0f}"
            ),
        )

    # Gate 3 — earnings proximity
    if liq.next_earnings_date is not None:
        trading_days = _trading_days_between(today, liq.next_earnings_date)
        if trading_days <= _MIN_EARNINGS_TRADING_DAYS:
            return FilterResult(
                filter_name=_FILTER_NAME,
                passed=False,
                metric_value=float(trading_days),
                threshold=float(_MIN_EARNINGS_TRADING_DAYS),
                reason=(
                    f"Earnings in {trading_days} trading days "
                    f"(≤ {_MIN_EARNINGS_TRADING_DAYS}-day blackout)"
                ),
            )

    # Gate 4 — pending M&A
    if liq.has_pending_ma:
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=False,
            metric_value=None,
            threshold=None,
            reason="Pending M&A event detected in recent 8-K filings",
        )

    return FilterResult(
        filter_name=_FILTER_NAME,
        passed=True,
        metric_value=liq.market_cap_usd,
        threshold=_MIN_MARKET_CAP_USD,
        reason=(
            f"Market cap ${liq.market_cap_usd:,.0f}; "
            f"ADV ${liq.adv_20d_usd:,.0f}; "
            f"no near-term earnings or M&A"
        ),
    )
