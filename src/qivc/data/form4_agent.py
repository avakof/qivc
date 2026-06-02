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

import asyncio
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from qivc.data import DataAgent, bulk_loader
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError, QivcRateLimitError
from qivc.schemas import InsiderTransaction

# Live-delta parse tuning (Task 8.3 part 2): the market-wide 14-day live window
# can be ~19k filings, each a sequential EDGAR fetch, so the parse is long-running.
_PROGRESS_EVERY = 100  # log + checkpoint every N filings...
_PROGRESS_SECONDS = 300.0  # ...or every 5 minutes, whichever first
_OBJ_RETRY_MAX = 5  # per-filing obj() retries on 403/429
_OBJ_RETRY_BASE_DELAY = 2.0  # seconds, exponential
_OBJ_RETRY_MAX_DELAY = 60.0

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

    def __init__(
        self,
        client: EdgarClient,
        db_path: str | None = None,
        recovery_dir: str = "data/form4_recovery",
    ) -> None:
        self._client = client
        self._db_path = db_path
        self._recovery_dir = Path(recovery_dir)

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
        """
        Parse the live-EDGAR delta. No cap — the full delta window is examined.

        Long-running (the market-wide 14-day window is ~19k sequential fetches),
        so this path is hardened for an unattended run:
          - progress is logged + checkpointed every 100 filings or 5 minutes;
          - per-filing obj() retries 403/429 with exponential back-off;
          - already-parsed filings (by accession) are skipped on resume, and a
            recovery file is persisted so an aborted run resumes without redoing
            successful work. The recovery file is removed on clean completion.
        """
        if live_start is None or live_start > end:
            return []
        live_lookback = (end - live_start).days
        try:
            filings_obj = await self._client.get_form4_filings(
                ticker=ticker, lookback_days=max(live_lookback, 0)
            )
        except Exception as exc:
            raise QivcDataError(f"Form4Agent live delta fetch failed: {exc}") from exc

        if filings_obj is None:
            return []
        count = len(filings_obj)

        processed, results = self._load_recovery(ticker, live_start, end)
        if processed:
            log.info(
                "Form4 live delta: resuming — %d filings already processed, %d P-txns recovered",
                len(processed),
                len(results),
            )
        log.info(
            "Form4 live delta: parsing %d filings (%s..%s), scope=%s",
            count,
            live_start,
            end,
            ticker or "market",
        )

        last_progress = time.monotonic()
        done_since_flush = 0
        for i in range(count):
            filing = filings_obj[i]
            accession = str(
                getattr(filing, "accession_no", "") or getattr(filing, "accession", "") or ""
            )
            if accession and accession in processed:
                continue  # already parsed in a prior (aborted) run

            try:
                ownership = await self._parse_filing_obj(filing)
                if ownership is not None:
                    results.extend(_parse_ownership(filing, ownership))
            except QivcRateLimitError:
                # Unrecoverable after back-off: persist progress and stop so the
                # run can resume rather than silently drop the remaining window.
                self._save_recovery(ticker, live_start, end, processed, results)
                raise QivcRateLimitError(
                    f"Form4 live delta aborted at filing {i + 1}/{count} after rate-limit "
                    f"back-off; recovery saved ({len(processed)} filings, {len(results)} txns)"
                ) from None
            except Exception as exc:
                log.debug("Skipping Form 4 filing %d (%s): %s", i, accession, exc)

            if accession:
                processed.add(accession)
            done_since_flush += 1

            now = time.monotonic()
            if done_since_flush >= _PROGRESS_EVERY or (now - last_progress) >= _PROGRESS_SECONDS:
                log.info(
                    "Form4 live delta progress: %d/%d filings examined, %d P-txns so far",
                    i + 1,
                    count,
                    len(results),
                )
                self._save_recovery(ticker, live_start, end, processed, results)
                last_progress = now
                done_since_flush = 0

        self._clear_recovery(ticker, live_start, end)
        log.info("Form4 live delta complete: %d filings examined, %d P-txns", count, len(results))
        return results

    async def _parse_filing_obj(self, filing: Any) -> Any:
        """Call filing.obj() with exponential back-off on 403/429 rate limits."""
        delay = _OBJ_RETRY_BASE_DELAY
        for attempt in range(_OBJ_RETRY_MAX):
            try:
                return filing.obj()
            except Exception as exc:
                msg = str(exc).lower()
                is_rate = "403" in msg or "429" in msg or "rate" in msg or "too many" in msg
                if is_rate and attempt < _OBJ_RETRY_MAX - 1:
                    wait = min(delay, _OBJ_RETRY_MAX_DELAY)
                    log.warning("obj() rate-limited, retry %d in %.0fs", attempt + 1, wait)
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
                if is_rate:
                    raise QivcRateLimitError(str(exc)) from exc
                raise
        return None

    # ----------------------------------------------------------- recovery I/O

    def _recovery_path(self, ticker: str | None, start: date, end: date) -> Path:
        scope = ticker or "market"
        return self._recovery_dir / f"form4_{scope}_{start}_{end}.json"

    def _load_recovery(
        self, ticker: str | None, start: date, end: date
    ) -> tuple[set[str], list[InsiderTransaction]]:
        """Restore (processed accessions, parsed transactions) from a prior run."""
        path = self._recovery_path(ticker, start, end)
        if not path.exists():
            return set(), []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            processed = set(data.get("processed_accessions", []))
            txns = [InsiderTransaction(**t) for t in data.get("transactions", [])]
            return processed, txns
        except Exception as exc:
            log.warning("Ignoring unreadable Form4 recovery file %s: %s", path, exc)
            return set(), []

    def _save_recovery(
        self,
        ticker: str | None,
        start: date,
        end: date,
        processed: set[str],
        results: list[InsiderTransaction],
    ) -> None:
        path = self._recovery_path(ticker, start, end)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "window": f"{start}..{end}",
                "scope": ticker or "market",
                "processed_accessions": sorted(processed),
                "transactions": [t.model_dump(mode="json") for t in results],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:  # checkpointing is best-effort
            log.warning("Failed to write Form4 recovery file %s: %s", path, exc)

    def _clear_recovery(self, ticker: str | None, start: date, end: date) -> None:
        self._recovery_path(ticker, start, end).unlink(missing_ok=True)
