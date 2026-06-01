"""
Point-in-time (PIT) data access for backtesting.

The cardinal rule: at a decision date `as_of`, only information that was
*publicly filed* strictly before `as_of` may be used. Form 4 / 10-K / 10-Q are
keyed on their FILING date (not the transaction or fiscal-period date), because
that is when the information actually became available to the market.

`get_sector_median_as_of` is the documented weak point: historical sector-ETF
constituents are not available here, so it returns the current proxy. Any
backtest using it is therefore subject to sector-composition look-ahead bias —
callers must record this in the result's `notes`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from qivc.data.edgar_client import EdgarClient
from qivc.data.form4_agent import _parse_ownership
from qivc.schemas import InsiderTransaction

log = logging.getLogger(__name__)

SECTOR_MEDIAN_LOOKAHEAD_NOTE = (
    "Sector medians use current ETF constituents (historical membership "
    "unavailable); backtest is subject to sector-composition look-ahead bias."
)


def filter_by_filing_date(
    transactions: list[InsiderTransaction],
    as_of: date,
) -> list[InsiderTransaction]:
    """
    Keep only transactions whose FILING date is strictly before *as_of*.

    Pure function — the heart of PIT correctness, unit-tested directly for the
    no-look-ahead guarantee.
    """
    return [t for t in transactions if t.filed_date < as_of]


async def get_form4_as_of(
    client: EdgarClient,
    as_of: date,
    lookback_days: int = 14,
    ticker: str | None = None,
) -> list[InsiderTransaction]:
    """
    Return Form 4 P-code transactions FILED in the *lookback_days* window ending
    strictly before *as_of*. Never returns a filing dated on or after *as_of*.
    """
    from datetime import timedelta

    # Fetch a slightly wider window then filter precisely by filing date.
    raw = await client.get_form4_filings(ticker=ticker, lookback_days=lookback_days + 1)
    if raw is None:
        return []

    transactions: list[InsiderTransaction] = []
    count = min(len(raw), 200)
    for i in range(count):
        filing = raw[i]
        try:
            ownership = filing.obj()
            if ownership is None:
                continue
            transactions.extend(_parse_ownership(filing, ownership))
        except Exception as exc:
            log.debug("PIT form4 skip filing %d: %s", i, exc)

    window_start = as_of - timedelta(days=lookback_days)
    return [t for t in filter_by_filing_date(transactions, as_of) if t.filed_date >= window_start]


async def get_fundamentals_as_of(
    client: EdgarClient,
    ticker: str,
    as_of: date,
) -> Any:
    """
    Return the most recent 10-K (preferred) or 10-Q Financials object FILED
    strictly before *as_of*. Returns None if nothing qualifies.

    edgartools exposes the filing date on each filing; we walk the most-recent
    filings and pick the first one filed before the decision date.
    """
    import edgar

    for form in ("10-K", "10-Q"):
        try:
            company = edgar.Company(ticker)
            filings = await client._run_with_retry(company.get_filings, form=form)
        except Exception as exc:
            log.debug("PIT fundamentals fetch failed for %s %s: %s", ticker, form, exc)
            continue
        if filings is None:
            continue
        for i in range(min(len(filings), 8)):
            filing = filings[i]
            filed = _filing_date(filing)
            if filed is not None and filed < as_of:
                fin = await client._run_with_retry(edgar.Financials.extract, filing)
                if fin is not None:
                    return fin
    return None


def get_sector_median_as_of(sector: str, as_of: date) -> float | None:
    """
    Historical sector-ETF constituents are unavailable here, so this returns
    None (no PIT sector median). Callers should treat the valuation gate as
    UNVERIFIABLE for backtests, or substitute the current proxy and record
    SECTOR_MEDIAN_LOOKAHEAD_NOTE. See module docstring.
    """
    log.debug("get_sector_median_as_of(%s, %s): no PIT data available", sector, as_of)
    return None


def _filing_date(filing: Any) -> date | None:
    raw = getattr(filing, "filing_date", None) or getattr(filing, "filed", None)
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
