"""InsiderHistoryAgent — per-CIK 3-year transaction history."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from qivc.data import DataAgent
from qivc.data.edgar_client import EdgarClient
from qivc.data.form4_agent import _parse_ownership
from qivc.exceptions import QivcDataError
from qivc.schemas import InsiderHistory, InsiderTransaction

log = logging.getLogger(__name__)


class InsiderHistoryAgent(DataAgent[InsiderHistory]):
    """Returns all Form 4 P-code transactions for a specific insider CIK."""

    def __init__(self, client: EdgarClient) -> None:
        self._client = client

    @property
    def source_name(self) -> str:
        return "sec_edgar_insider_history"

    async def fetch(self, **kwargs: Any) -> InsiderHistory:
        """
        Keyword args:
            cik (str): insider's CIK
            years (int): how many years of history (default 3)
        """
        cik: str = str(kwargs["cik"])
        years: int = int(kwargs.get("years", 3))

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

        today = date.today()
        actual_years = min(years, max(1, today.year - (today.year - years)))

        return InsiderHistory(
            cik=cik,
            transactions=transactions,
            years_of_history=actual_years,
        )
