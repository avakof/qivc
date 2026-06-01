"""Form4Agent — fetches and parses Form 4 P-code transactions."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from qivc.data import DataAgent
from qivc.data.edgar_client import EdgarClient
from qivc.exceptions import QivcDataError
from qivc.schemas import InsiderTransaction

log = logging.getLogger(__name__)

_PURCHASE_CODE = "P"


def _parse_ownership(filing: Any, ownership: Any) -> list[InsiderTransaction]:
    """Convert an Ownership object into InsiderTransaction records, P-code only."""
    transactions: list[InsiderTransaction] = []

    issuer = ownership.issuer
    ticker: str = str(issuer.ticker or "")

    reporting_owners = ownership.reporting_owners
    if not reporting_owners or not reporting_owners.owners:
        return transactions

    # Prefer a human owner over a corporate entity
    owner = reporting_owners.owners[0]
    for o in reporting_owners.owners:
        if not getattr(o, "is_company", True):
            owner = o
            break

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
            )
        )
    return transactions


class Form4Agent(DataAgent[list[InsiderTransaction]]):
    """Fetches Form 4 open-market purchase (code P) transactions."""

    def __init__(self, client: EdgarClient) -> None:
        self._client = client

    @property
    def source_name(self) -> str:
        return "sec_edgar_form4"

    async def fetch(self, **kwargs: Any) -> list[InsiderTransaction]:
        """
        Keyword args:
            ticker (str | None): scopes to a specific company; None for global scan
            lookback_days (int): window in days (default 14)
        """
        ticker: str | None = kwargs.get("ticker")
        lookback_days: int = int(kwargs.get("lookback_days", 14))

        try:
            filings_obj = await self._client.get_form4_filings(
                ticker=ticker, lookback_days=lookback_days
            )
        except Exception as exc:
            raise QivcDataError(f"Form4Agent.fetch failed: {exc}") from exc

        results: list[InsiderTransaction] = []
        if filings_obj is None:
            return results

        for i in range(len(filings_obj)):
            filing = filings_obj[i]
            try:
                ownership = filing.obj()
                if ownership is None:
                    continue
                results.extend(_parse_ownership(filing, ownership))
            except Exception as exc:
                log.debug("Skipping Form 4 filing %d: %s", i, exc)

        return results
