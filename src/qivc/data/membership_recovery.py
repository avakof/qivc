"""
2022-only delisted-name recovery for IWM PIT membership (Task 14 Phase B recovery).

OpenFIGI leaves ~9% of the 2022 N-PORT holdings unmapped because their 2022 CUSIPs
are retired (delisted / reverse-split names). This ONE recovery pass re-resolves
those unmapped 2022 holdings to a ticker via two free routes, marking the
``mapping_source`` so each recovery is auditable:

  1. ``sec_name``    — normalized company-name match to SEC company_tickers.json
                       (recovers names still SEC-registered under a ticker).
  2. ``form25_cik``  — name -> CIK via the loaded Form 25/15 delisting filings,
                       then CIK -> ticker via company_tickers or form4_historical.

SCOPE: 2022 only (2025 is 97.9% as-is). ONE pass; the caller stops and reports
afterward regardless of where coverage lands. The residual stays flagged + counted.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from qivc.storage.db import get_connection

log = logging.getLogger(__name__)

_SUFFIX = (
    " INCORPORATED", " INC", " CORPORATION", " CORP", " COMPANY", " CO", " LTD",
    " LIMITED", " PLC", " HOLDINGS", " HOLDING", " GROUP", " CLASS A", " CLASS B",
    " THE", " COS",
)


def normalize_name(s: str) -> str:
    s = s.upper().split("/")[0]
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    for w in _SUFFIX:
        s = s.replace(w, " ")
    return re.sub(r"\s+", " ", s).strip()


def recover_membership(
    db_path: str,
    company_tickers: dict[str, Any],
    *,
    as_of: str = "2022-06-30",
    year: int = 2022,
) -> dict[str, int]:
    """
    Run the one recovery pass for *as_of*; update iwm_pit_constituents in place.
    *company_tickers* is the raw SEC company_tickers.json mapping. Returns a report.
    """
    name2tk: dict[str, str] = {}
    cik2tk_cur: dict[str, str] = {}
    for v in company_tickers.values():
        name2tk.setdefault(normalize_name(v["title"]), v["ticker"])
        cik2tk_cur[str(v["cik_str"])] = v["ticker"]

    with get_connection(db_path) as conn:
        def _count(sql: str) -> int:
            row = conn.execute(sql, [as_of]).fetchone()
            return int(row[0]) if row else 0

        before = _count(
            "SELECT count(*) FROM iwm_pit_constituents WHERE as_of_date = ? AND mapped_flag"
        )
        total = _count("SELECT count(*) FROM iwm_pit_constituents WHERE as_of_date = ?")
        unmapped = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM iwm_pit_constituents "
                "WHERE as_of_date = ? AND NOT mapped_flag",
                [as_of],
            ).fetchall()
        ]
        name2cik = {
            normalize_name(n): str(int(c))
            for n, c in conn.execute(
                "SELECT company_name, cik FROM delistings_historical"
            ).fetchall()
        }
        cik2tk_f4: dict[str, str] = {}
        for cik, tk in conn.execute(
            "SELECT DISTINCT cik, ticker FROM form4_historical WHERE ticker <> ''"
        ).fetchall():
            cik2tk_f4.setdefault(str(int(cik)), tk)
        cik_buy = {
            str(int(r[0]))
            for r in conn.execute(
                "SELECT DISTINCT cik FROM form4_historical "
                "WHERE filed_date >= ? AND filed_date <= ?",
                [f"{year}-01-01", f"{year}-12-31"],
            ).fetchall()
        }

        rec_name = rec_cik = 0
        residual: list[str] = []
        residual_cik: dict[str, str] = {}
        for name in unmapped:
            nn = normalize_name(name)
            ticker: str | None = None
            source = ""
            if nn in name2tk:
                ticker, source = name2tk[nn], "sec_name"
            else:
                cik = name2cik.get(nn)
                if cik and cik in cik2tk_cur:
                    ticker, source = cik2tk_cur[cik], "form25_cik"
                elif cik and cik in cik2tk_f4:
                    ticker, source = cik2tk_f4[cik], "form25_cik"
                if cik:
                    residual_cik[name] = cik
            if ticker is None:
                residual.append(name)
                continue
            conn.execute(
                "UPDATE iwm_pit_constituents SET ticker = ?, mapping_source = ?, "
                "mapped_flag = TRUE WHERE as_of_date = ? AND name = ? AND NOT mapped_flag",
                [ticker, source, as_of, name],
            )
            if source == "sec_name":
                rec_name += 1
            else:
                rec_cik += 1
        conn.commit()

        # True survivor-bias-touching-strategy measure: residual names whose CIK
        # had insider buying (form4 purchases) in the test year.
        resid_with_buy = sum(
            1 for n in residual if residual_cik.get(n) in cik_buy
        )
        resid_no_cik = sum(1 for n in residual if n not in residual_cik)
        after = before + rec_name + rec_cik

    report = {
        "total": total, "before": before, "after": after,
        "recovered_sec_name": rec_name, "recovered_form25_cik": rec_cik,
        "residual": len(residual), "residual_with_insider_buying": resid_with_buy,
        "residual_no_cik": resid_no_cik,
    }
    log.info("2022 recovery: %s", report)
    return report
