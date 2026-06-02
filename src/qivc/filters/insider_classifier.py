"""
Insider classification per Cohen-Malloy-Pomorski (2012) — pure function.

Rule: An insider is *routine* if they traded in the SAME calendar month in
each of the 3 consecutive calendar years immediately preceding the candidate
transaction's year.  Everyone else is *opportunistic*.  Insiders with fewer
than 3 years of recorded history are classified *unclassified* and are
conservatively excluded from opportunistic counts.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from qivc.schemas import InsiderHistory, InsiderTransaction

ClassificationResult = Literal["opportunistic", "routine", "unclassified"]


def compute_years_of_history(
    transactions: list[InsiderTransaction],
    as_of_date: date,
) -> int:
    """
    Count the number of distinct **prior calendar years** (strictly before
    ``as_of_date``'s year) in which the insider made a P-code purchase.

    Per Cohen-Malloy-Pomorski, classifiability turns on whether the insider has
    *traded across* the prior calendar years — not on elapsed time since their
    first trade. This replaces the OQ-2 duration proxy
    (``floor((today - earliest_filed) / 365.25)``), which structurally capped at
    the bulk store's ~3-year depth and left ~99% of insiders "unclassified" even
    when they had verifiable multi-year trading records (see OQ-4 in
    STRATEGY_NOTES). Counting by *calendar year* (not a rolling window) is closer
    to CMP's literal "each of the prior three consecutive years".
    """
    return len(
        {
            t.filed_date.year
            for t in transactions
            if t.transaction_code == "P" and t.filed_date.year < as_of_date.year
        }
    )


def classify(
    transaction: InsiderTransaction,
    history: InsiderHistory,
) -> ClassificationResult:
    """
    Classify a single transaction as opportunistic, routine, or unclassified.

    Parameters
    ----------
    transaction:
        The candidate transaction to classify.
    history:
        All historical Form 4 transactions for the same insider (same CIK),
        covering at least 3 years.

    Returns
    -------
    "routine"       - insider traded in the same calendar month in each of
                      year-1, year-2, and year-3 (relative to transaction year)
    "opportunistic" - history spans ≥3 years but the routine pattern is absent
    "unclassified"  - fewer than 3 years of history; do not assume opportunistic
    """
    # Classifiability gate, computed directly from the trading record relative to
    # the candidate transaction (OQ-4): >= 3 distinct prior calendar years.
    if compute_years_of_history(history.transactions, transaction.transaction_date) < 3:
        return "unclassified"

    tx_month = transaction.transaction_date.month
    tx_year = transaction.transaction_date.year

    # The three prior calendar years that must each contain a same-month trade
    required_years = {tx_year - 1, tx_year - 2, tx_year - 3}

    years_with_trade: set[int] = set()
    for t in history.transactions:
        y = t.transaction_date.year
        if y in required_years and t.transaction_date.month == tx_month:
            years_with_trade.add(y)

    if years_with_trade == required_years:
        return "routine"

    return "opportunistic"
