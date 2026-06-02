"""InsiderHistoryAgent — per-CIK 3-year transaction history for CMP classification.

Bulk-first (Task 8.3 part 1): the 3-year history needed to classify an insider
is read from the local bulk store (``form4_historical``) whenever the store
reaches far enough back to cover the CMP lookback window — turning a throttled
live-EDGAR call per CIK (the dominant cost of a full screen) into a local disk
read. Live EDGAR is used only when the bulk store cannot cover the window
(no store, empty store, or it does not reach back ``years`` years).

``years_of_history`` is the count of distinct prior calendar years with P-code
purchases (OQ-4 — see ``insider_classifier.compute_years_of_history``), not a
duration. The bulk read window is aligned to calendar years (Jan 1 of
``today.year - years``) so the earliest year is fully captured.

Why per-CIK live fallback is NOT needed when the store covers the window: the
bulk store holds complete quarters from 2023q1, so its old-end coverage is a
*superset* of ``get_insider_history(years=3)`` (which only reaches back ~3
years). The only filings bulk lacks are the current, unpublished quarter —
which are *recent*, never part of the prior calendar years CMP inspects. So a
CIK absent from a covering bulk window is **definitively** unclassified, and
fetching it live would only confirm that at network cost.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from qivc.data import DataAgent, bulk_loader
from qivc.data.edgar_client import EdgarClient
from qivc.data.form4_agent import _parse_ownership
from qivc.exceptions import QivcDataError
from qivc.schemas import InsiderHistory, InsiderTransaction

log = logging.getLogger(__name__)


def _years_of_history(transactions: list[InsiderTransaction]) -> int:
    """Distinct prior-calendar-year count for the audit/display field (OQ-4).

    Uses a deferred import of the classifier's ``compute_years_of_history`` so
    ``qivc.data`` keeps no top-level dependency on ``qivc.filters`` (layering;
    see test_data_module_does_not_import_filters). The classifier independently
    recomputes the gate per candidate, so this value is informational only.
    """
    from qivc.filters.insider_classifier import compute_years_of_history

    return compute_years_of_history(transactions, date.today())


class InsiderHistoryAgent(DataAgent[InsiderHistory]):
    """Returns all Form 4 P-code transactions for a specific insider CIK."""

    def __init__(self, client: EdgarClient, db_path: str | None = None) -> None:
        self._client = client
        self._db_path = db_path
        # Per-run in-memory cache (each CIK is read at most once per screen, but
        # cheap insurance against accidental repeat reads).
        self._cache: dict[tuple[str, int], InsiderHistory] = {}
        # Store-level metadata (cutover boundary + coverage start) is constant
        # for a run; resolve it once instead of opening a connection per CIK.
        self._meta_loaded = False
        self._cutover: date | None = None
        self._coverage_start: date | None = None

    def _store_meta(self) -> tuple[date | None, date | None]:
        if not self._meta_loaded and self._db_path:
            self._cutover = bulk_loader.bulk_cutover_date(self._db_path)
            # Coverage floor = start of the earliest COMPLETE quarter, so a CMP
            # window aligned to calendar years is considered covered even though
            # the first actual filing lands a few days into the quarter.
            self._coverage_start = bulk_loader.earliest_complete_quarter_start(self._db_path)
        self._meta_loaded = True
        return self._cutover, self._coverage_start

    @property
    def source_name(self) -> str:
        return "sec_insider_history_bulk+live"

    async def fetch(self, **kwargs: Any) -> InsiderHistory:
        """
        Keyword args:
            cik (str): insider's CIK
            years (int): how many years of history (default 3)
        """
        cik: str = str(kwargs["cik"])
        years: int = int(kwargs.get("years", 3))

        cached = self._cache.get((cik, years))
        if cached is not None:
            return cached

        result = self._fetch_from_bulk(cik, years)
        if result is None:
            result = await self._fetch_from_live_edgar(cik, years)
        self._cache[(cik, years)] = result
        return result

    # ------------------------------------------------------------------

    def _fetch_from_bulk(self, cik: str, years: int) -> InsiderHistory | None:
        """
        Read the CMP window from the bulk store, or ``None`` to signal the caller
        to fall back to live EDGAR (store absent / empty / too shallow).

        When the store *does* cover the window, the result is authoritative even
        if empty (= no qualifying history → unclassified); no per-CIK live call.
        """
        if not self._db_path:
            return None
        cutover, coverage_start = self._store_meta()
        if cutover is None:
            return None  # empty store → live
        # Calendar-year-aligned window: to classify a candidate in year Y we need
        # the prior `years` calendar years, i.e. from Jan 1 of (Y - years). This
        # captures all of the earliest year (e.g. full 2023), which a rolling
        # `today - years*365.25` window would clip (OQ-4).
        window_start = date(date.today().year - years, 1, 1)
        if coverage_start is None or coverage_start > window_start:
            return None  # store does not reach back far enough → live

        txns = bulk_loader.read_purchases_for_cik(self._db_path, cik, window_start, cutover)
        log.debug("insider_history bulk hit: cik=%s n=%d", cik, len(txns))
        return InsiderHistory(
            cik=cik,
            transactions=txns,
            years_of_history=_years_of_history(txns),
        )

    async def _fetch_from_live_edgar(self, cik: str, years: int) -> InsiderHistory:
        """Original live path: fetch the CIK's Form 4 history from EDGAR."""
        try:
            filings_obj = await self._client.get_insider_history(cik=cik, years=years)
        except Exception as exc:
            raise QivcDataError(f"InsiderHistoryAgent.fetch failed for cik={cik}: {exc}") from exc

        transactions: list[InsiderTransaction] = []
        if filings_obj is not None:
            for i in range(len(filings_obj)):
                filing = filings_obj[i]
                try:
                    ownership = filing.obj()
                    if ownership is None:
                        continue
                    transactions.extend(_parse_ownership(filing, ownership))
                except Exception as exc:
                    log.debug("Skipping filing %d for cik=%s: %s", i, cik, exc)

        return InsiderHistory(
            cik=cik,
            transactions=transactions,
            years_of_history=_years_of_history(transactions),
        )
