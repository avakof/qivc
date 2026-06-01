"""Rate-limited, async wrapper around edgartools."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, timedelta
from functools import partial
from typing import Any

import edgar

from qivc.exceptions import QivcDataError, QivcRateLimitError

log = logging.getLogger(__name__)

_RETRY_MAX = 5
_RETRY_DELAY_MIN = 1.0
_RETRY_DELAY_MAX = 60.0
_RETRY_FACTOR = 2.0


class _RateLimiter:
    """Token-bucket: allows at most `rps` acquisitions per second."""

    def __init__(self, rps: int) -> None:
        self._sem = asyncio.Semaphore(rps)

    async def acquire(self) -> None:
        await self._sem.acquire()
        loop = asyncio.get_event_loop()
        loop.call_later(1.0, self._sem.release)


class EdgarClient:
    """
    Async facade over edgartools. All blocking edgartools calls are dispatched
    to a thread-pool executor; a semaphore limits throughput to `rate_limit_rps`
    calls per second. 429/403 responses trigger exponential back-off with jitter.
    """

    def __init__(self, user_agent: str, rate_limit_rps: int = 8) -> None:
        edgar.set_identity(user_agent)
        self._limiter = _RateLimiter(rps=rate_limit_rps)
        self._user_agent = user_agent

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    async def _run(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Acquire a rate-limit slot then run *func* in the thread pool."""
        await self._limiter.acquire()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def _run_with_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run *func* through the rate limiter with exponential back-off on 429/403."""
        delay = _RETRY_DELAY_MIN
        last_exc: Exception | None = None
        for attempt in range(_RETRY_MAX):
            try:
                return await self._run(func, *args, **kwargs)
            except Exception as exc:
                msg = str(exc).lower()
                if "429" in msg or "403" in msg or "rate" in msg:
                    last_exc = exc
                    if attempt < _RETRY_MAX - 1:
                        jitter = random.uniform(0, delay * 0.1)
                        wait = min(delay + jitter, _RETRY_DELAY_MAX)
                        log.warning(
                            "edgar rate limit hit, retry %d/%d in %.1fs",
                            attempt + 1,
                            _RETRY_MAX,
                            wait,
                        )
                        await asyncio.sleep(wait)
                        delay = min(delay * _RETRY_FACTOR, _RETRY_DELAY_MAX)
                        continue
                    raise QivcRateLimitError(
                        f"EDGAR rate limit exhausted after {_RETRY_MAX} retries"
                    ) from exc
                raise QivcDataError(f"EDGAR request failed: {exc}") from exc
        raise QivcRateLimitError(
            f"EDGAR rate limit exhausted after {_RETRY_MAX} retries"
        ) from last_exc

    # ------------------------------------------------------------------
    # public async methods
    # ------------------------------------------------------------------

    async def get_form4_filings(
        self,
        ticker: str | None = None,
        lookback_days: int = 14,
    ) -> Any:
        """
        Return a filing collection for the last *lookback_days*.
        Scoped to *ticker* if given, otherwise a global EFTS search (limit 100).
        """
        end = date.today()
        start = end - timedelta(days=lookback_days)
        date_range = f"{start}:{end}"

        if ticker:
            company = edgar.Company(ticker)
            return await self._run_with_retry(company.get_filings, form="4", date=date_range)
        return await self._run_with_retry(
            edgar.search_filings,
            forms="4",
            start_date=str(start),
            end_date=str(end),
            limit=100,
        )

    async def get_insider_history(self, cik: str, years: int = 3) -> Any:
        """Return Form 4 filings for CIK over the last *years* years."""
        end = date.today()
        start = date(end.year - years, end.month, end.day)
        date_range = f"{start}:{end}"
        entity = edgar.Entity(int(cik))
        return await self._run_with_retry(entity.get_filings, form="4", date=date_range)

    async def get_financials_10k(self, ticker: str, periods: int = 2) -> list[Any]:
        """Return Financials objects from the last *periods* 10-K filings (most-recent first)."""
        company = edgar.Company(ticker)
        filings_obj: Any = await self._run_with_retry(company.get_filings, form="10-K")
        results: list[Any] = []
        for i in range(min(periods, len(filings_obj))):
            filing = filings_obj[i]
            fin = await self._run_with_retry(edgar.Financials.extract, filing)
            if fin is not None:
                results.append(fin)
        return results

    async def get_financials_10q(self, ticker: str, periods: int = 4) -> list[Any]:
        """Return Financials objects from the last *periods* 10-Q filings."""
        company = edgar.Company(ticker)
        filings_obj: Any = await self._run_with_retry(company.get_filings, form="10-Q")
        results: list[Any] = []
        for i in range(min(periods, len(filings_obj))):
            filing = filings_obj[i]
            fin = await self._run_with_retry(edgar.Financials.extract, filing)
            if fin is not None:
                results.append(fin)
        return results

    async def get_recent_8k_texts(self, ticker: str, lookback_days: int = 60) -> list[str]:
        """Return the text content of recent 8-K filings for M&A detection."""
        end = date.today()
        start = end - timedelta(days=lookback_days)
        date_range = f"{start}:{end}"
        company = edgar.Company(ticker)
        filings_obj: Any = await self._run_with_retry(
            company.get_filings, form="8-K", date=date_range
        )
        texts: list[str] = []
        for i in range(min(20, len(filings_obj))):
            filing = filings_obj[i]
            try:

                def _get_text(f: Any) -> str:
                    doc = f.document
                    return doc.text if doc else ""

                text: str = await self._run_with_retry(_get_text, filing)
                texts.append(text)
            except Exception:
                texts.append("")
        return texts

    @property
    def user_agent(self) -> str:
        return self._user_agent
