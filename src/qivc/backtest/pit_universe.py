"""
Point-in-time universe providers (Task 14 Phase B4; the Task-13 protocol that was
never built).

`PitUniverseProvider` abstracts "what was the investable universe on date X, and
what happened to a name that delists mid-hold." Two implementations:

  - `SecPitProvider` — survivor-bias-free. Constituents from IWM N-PORT membership
    (`iwm_pit_constituents`); delisting events from `delistings_historical`; exit
    marks from the conservative B3 rules.
  - `BiasedSnapshot` — the old behaviour (one current IWM snapshot for every date),
    kept for regression/comparison so we can quantify the bias the fix removes.

WATCH-2 (no look-ahead): `SecPitProvider.get_constituents(as_of)` uses the most
recent membership snapshot whose N-PORT was **FILED STRICTLY BEFORE** *as_of* —
never a contemporaneous or future filing. See `test_nport_membership_no_lookahead`.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qivc.backtest.delisting_returns import classify_reason, delisting_exit_price
from qivc.storage.db import get_connection


@dataclass(frozen=True)
class DelistingEvent:
    ticker: str | None
    cik: str
    company_name: str
    form_type: str
    filed_date: _dt.date
    reason: str


@runtime_checkable
class PitUniverseProvider(Protocol):
    def get_constituents(self, as_of: _dt.date) -> set[str]:
        """Tickers investable as of *as_of* (point-in-time, no look-ahead)."""
        ...

    def get_delisting_event(self, ticker: str) -> DelistingEvent | None:
        """The delisting/deregistration event for *ticker*, if any."""
        ...

    def delisted_exit_price(
        self, ticker: str, last_price: float, hold_start: _dt.date, hold_end: _dt.date
    ) -> float | None:
        """Conservative exit price if *ticker* delists within (hold_start, hold_end]."""
        ...


def _normalize_name(s: str) -> str:
    import re

    s = s.upper().split("/")[0]
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    for w in (" INCORPORATED", " INC", " CORPORATION", " CORP", " COMPANY", " CO",
              " LTD", " LIMITED", " PLC", " LLC", " LP", " HOLDINGS", " HOLDING",
              " GROUP", " CLASS A", " CLASS B", " THE", " TRUST"):
        s = s.replace(w, " ")
    return re.sub(r"\s+", " ", s).strip()


class SecPitProvider:
    """Survivor-bias-free provider backed by the DuckDB PIT tables."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def get_constituents(self, as_of: _dt.date) -> set[str]:
        with get_connection(self._db_path) as conn:
            # WATCH-2: latest snapshot whose N-PORT was FILED strictly before as_of.
            row = conn.execute(
                """SELECT max(as_of_date) FROM iwm_pit_constituents
                   WHERE filed_date < ?""",
                [as_of],
            ).fetchone()
            snap = row[0] if row else None
            if snap is None:
                return set()
            tickers = conn.execute(
                """SELECT DISTINCT ticker FROM iwm_pit_constituents
                   WHERE as_of_date = ? AND mapped_flag AND ticker IS NOT NULL""",
                [snap],
            ).fetchall()
            return {t[0] for t in tickers}

    def get_delisting_event(self, ticker: str) -> DelistingEvent | None:
        with get_connection(self._db_path) as conn:
            # Prefer a row whose ticker resolved directly; else match by the
            # constituent's (N-PORT) company name -> delisting company_name.
            row = conn.execute(
                """SELECT ticker, cik, company_name, form_type, filed_date, reason
                   FROM delistings_historical WHERE ticker = ?
                   ORDER BY filed_date LIMIT 1""",
                [ticker],
            ).fetchone()
            if row is None:
                names = conn.execute(
                    """SELECT DISTINCT name FROM iwm_pit_constituents WHERE ticker = ?""",
                    [ticker],
                ).fetchall()
                norms = {_normalize_name(n[0]) for n in names}
                if norms:
                    cand = conn.execute(
                        """SELECT ticker, cik, company_name, form_type, filed_date, reason
                           FROM delistings_historical ORDER BY filed_date"""
                    ).fetchall()
                    for c in cand:
                        if _normalize_name(c[2]) in norms:
                            row = c
                            break
            if row is None:
                return None
            return DelistingEvent(
                ticker=row[0], cik=row[1], company_name=row[2],
                form_type=row[3], filed_date=row[4], reason=row[5],
            )

    def delisted_exit_price(
        self, ticker: str, last_price: float, hold_start: _dt.date, hold_end: _dt.date
    ) -> float | None:
        ev = self.get_delisting_event(ticker)
        if ev is None or not (hold_start < ev.filed_date <= hold_end):
            return None
        return delisting_exit_price(ev.reason, last_price)

    # Reason enrichment is optional and on-demand (Form 25/15 carry no clean
    # reason at load). classify_reason() can be applied to fetched filing text
    # for the small set of held-then-delisted names in a backtest.
    @staticmethod
    def reason_from_text(text: str | None) -> str:
        return classify_reason(text)


class BiasedSnapshot:
    """The pre-fix behaviour: one current IWM snapshot for every date (for comparison)."""

    def __init__(self, tickers: set[str] | None = None) -> None:
        if tickers is None:
            from qivc.backtest.universe import iwm_tickers

            tickers = iwm_tickers()
        self._tickers = tickers

    def get_constituents(self, as_of: _dt.date) -> set[str]:
        return set(self._tickers)

    def get_delisting_event(self, ticker: str) -> DelistingEvent | None:
        return None  # the bias: delisted names are simply never modelled

    def delisted_exit_price(
        self, ticker: str, last_price: float, hold_start: _dt.date, hold_end: _dt.date
    ) -> float | None:
        return None
