"""Form4Agent — sources Form 4 P-code transactions for the scan window.

Hybrid source (Task 8.2): the bulk store (``form4_historical``) is authoritative
for filings filed on or before the most recent complete bulk quarter; live EDGAR
covers only the *delta* filed after that boundary. This replaces the old
market-wide path that fetched the live EDGAR index and parsed at most
``_GLOBAL_SCAN_LIMIT`` (100) filings — examining <1% of the universe. The cap is
gone; the full window is now examined.

NOTE: the bulk store, being quarterly, never contains the current (unpublished)
quarter, so a short trailing daily window is typically served entirely by the
live delta; the bulk store serves longer / historical windows and the CMP
3-year history. See BULK_DATA_NOTES.md (§7) and STRATEGY_NOTES.md.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from qivc.data import DataAgent, bulk_loader
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError
from qivc.schemas import InsiderTransaction

log = logging.getLogger(__name__)

_PURCHASE_CODE = "P"

# TODO(task-8.2/8.3): the bulk SEC source carries a few raw price anomalies — e.g.
# ticker REEMF reported TRANS_PRICEPERSHARE ~= $29M/share, yielding a ~$7e15
# value_usd; ~9 of 5,509 kept P-rows in 2024q1 had price > $10k. The strict bulk
# filter only drops non-positive prices, so these survive into value-based gates
# (Track B's $250k threshold). A value/price sanity-bound is needed but is NOT
# applied here — surfaced for a separate decision on the right ceiling. See
# BULK_DATA_NOTES.md §6.


def _parse_ownership(filing: Any, ownership: Any) -> list[InsiderTransaction]:
    """Convert an Ownership object into InsiderTransaction records, P-code only."""
    transactions: list[InsiderTransaction] = []

    issuer = ownership.issuer
    ticker: str = str(issuer.ticker or "")

    reporting_owners = ownership.reporting_owners
    if not reporting_owners or not reporting_owners.owners:
        return transactions

    # Prefer a human owner over a corporate entity. (The live path attributes a
    # filing's transactions to a single chosen owner — it does not fan out
    # co-filers, so it needs no accession de-dup. The bulk path fans out and is
    # de-duped downstream; see cluster_detector.dedupe_by_accession.)
    owner = reporting_owners.owners[0]
    for o in reporting_owners.owners:
        if not getattr(o, "is_company", True):
            owner = o
            break

    accession: str = str(
        getattr(filing, "accession_no", "") or getattr(filing, "accession", "") or ""
    )

    nd_table = ownership.non_derivative_table
    if nd_table is None or getattr(nd_table, "empty", True):
        return transactions

    try:
        # NonDerivativeTable.transactions is a NonDerivativeTransactions wrapper
        nd_txns = getattr(nd_table, "transactions", None)
        if nd_txns is None or getattr(nd_txns, "empty", True):
            return transactions
        df = nd_txns.data
    except Exception as exc:
        log.debug("Could not read nd_table.transactions.data: %s", exc)
        return transactions

    for _, row in df.iterrows():
        code: str = str(row.get("Code", "")).strip().upper()
        if code != _PURCHASE_CODE:
            continue

        try:
            raw_date = str(row.get("Date", ""))
            tx_date = date.fromisoformat(raw_date[:10]) if raw_date else date.today()
        except ValueError:
            tx_date = date.today()

        try:
            filed_raw = str(filing.filed) if hasattr(filing, "filed") else ""
            filed_dt = date.fromisoformat(filed_raw[:10]) if filed_raw else date.today()
        except ValueError:
            filed_dt = date.today()

        try:
            shares = float(row.get("Shares", 0) or 0)
            price = float(row.get("Price", 0) or 0)
        except (TypeError, ValueError):
            shares, price = 0.0, 0.0

        transactions.append(
            InsiderTransaction(
                cik=str(getattr(owner, "cik", "") or ""),
                name=str(getattr(owner, "name", "") or ""),
                title=str(
                    getattr(owner, "officer_title", None) or getattr(owner, "position", None) or ""
                ),
                ticker=ticker,
                shares=shares,
                price=price,
                value_usd=shares * price,
                transaction_date=tx_date,
                filed_date=filed_dt,
                transaction_code=code,
                is_director=bool(getattr(owner, "is_director", False)),
                is_officer=bool(getattr(owner, "is_officer", False)),
                is_ten_percent_owner=bool(getattr(owner, "is_ten_pct_owner", False)),
                accession_number=accession,
            )
        )
    return transactions


class Form4Agent(DataAgent[list[InsiderTransaction]]):
    """Sources Form 4 open-market purchases (code P) from the bulk store + live delta."""

    def __init__(self, client: EdgarClient, db_path: str | None = None) -> None:
        self._client = client
        self._db_path = db_path

    @property
    def source_name(self) -> str:
        return "sec_form4_bulk+live"

    async def fetch(self, **kwargs: Any) -> list[InsiderTransaction]:
        """
        Keyword args:
            ticker (str | None): scopes to a specific company; None for full scan
            lookback_days (int): window in days (default 14)

        Returns the full multi-owner fan-out (de-dup for clusters happens
        downstream). Bulk records win over live on any (cik, accession) overlap.
        """
        ticker: str | None = kwargs.get("ticker")
        lookback_days: int = int(kwargs.get("lookback_days", 14))

        end = date.today()
        start = end - timedelta(days=lookback_days)

        bulk_txns = self._read_bulk(ticker, start, end)
        live_start = self._live_window_start(start)
        live_txns = await self._fetch_live(ticker, live_start, end, lookback_days)

        # Cross-source de-dup: bulk is authoritative, so drop any live record
        # whose (cik, accession) already came from bulk.
        seen = {(t.cik, t.accession_number) for t in bulk_txns if t.accession_number}
        merged = list(bulk_txns)
        for t in live_txns:
            if t.accession_number and (t.cik, t.accession_number) in seen:
                continue
            merged.append(t)
        return merged

    # ------------------------------------------------------------------

    def _read_bulk(self, ticker: str | None, start: date, end: date) -> list[InsiderTransaction]:
        """Read the bulk-covered portion of the window, or nothing if unavailable."""
        if not self._db_path:
            return []
        cutover = bulk_loader.bulk_cutover_date(self._db_path)
        if cutover is None or start > cutover:
            return []  # window is entirely in the live delta
        bulk_end = min(end, cutover)
        txns = bulk_loader.read_purchases(self._db_path, start, bulk_end, ticker=ticker)
        log.info("Form4 bulk read: %d records for %s..%s", len(txns), start, bulk_end)
        return txns

    def _live_window_start(self, start: date) -> date | None:
        """First day the live delta must cover (the day after the bulk boundary)."""
        if not self._db_path:
            return start  # no bulk store -> live covers the whole window
        cutover = bulk_loader.bulk_cutover_date(self._db_path)
        if cutover is None:
            return start
        delta_start = cutover + timedelta(days=1)
        return max(start, delta_start)

    async def _fetch_live(
        self,
        ticker: str | None,
        live_start: date | None,
        end: date,
        lookback_days: int,
    ) -> list[InsiderTransaction]:
        """Parse the live-EDGAR delta. No cap — the full delta window is examined."""
        if live_start is None or live_start > end:
            return []
        live_lookback = (end - live_start).days
        try:
            filings_obj = await self._client.get_form4_filings(
                ticker=ticker, lookback_days=max(live_lookback, 0)
            )
        except Exception as exc:
            raise QivcDataError(f"Form4Agent live delta fetch failed: {exc}") from exc

        results: list[InsiderTransaction] = []
        if filings_obj is None:
            return results

        count = len(filings_obj)
        if ticker is None:
            log.info("Form4 live delta: parsing %d filings (%s..%s)", count, live_start, end)

        # The EDGAR query is already scoped to [live_start, end] by filing date,
        # so every returned filing is within the delta window; no cap is applied.
        for i in range(count):
            filing = filings_obj[i]
            try:
                ownership = filing.obj()
                if ownership is None:
                    continue
                results.extend(_parse_ownership(filing, ownership))
            except Exception as exc:
                log.debug("Skipping Form 4 filing %d: %s", i, exc)
        return results
