#!/usr/bin/env python
"""
Load the Task-14 PIT data: IWM N-PORT membership + SEC Form 25/15 delistings.

    uv run python scripts/load_pit_data.py membership      # IWM N-PORT 2021-2025
    uv run python scripts/load_pit_data.py delistings      # Form 25/15 2021q1-2025q4
    uv run python scripts/load_pit_data.py both

Membership uses OpenFIGI (free, rate-limited) for CUSIP/ISIN->ticker and caches
every lookup to data/pit/openfigi_cache.json — re-runs are instant and never
re-hit the API. Delistings download the EDGAR quarterly full index. Both are
idempotent. Reports per-year mapped/unmapped and per-quarter row counts.
"""

from __future__ import annotations

import logging
import sys

import httpx

from qivc.config import Settings
from qivc.data import bulk_loader, delisting_loader, membership_recovery, nport_loader

YEARS = [2021, 2022, 2023, 2024, 2025]
QUARTERS = bulk_loader.quarters_in_range("2021q1", "2025q4")


def _cik_to_ticker(ua: str) -> dict[str, str]:
    """SEC company_tickers.json -> {cik(int as str): ticker} for active issuers."""
    with httpx.Client(timeout=60.0) as c:
        data = c.get(
            "https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": ua}
        ).json()
    return {str(v["cik_str"]): v["ticker"] for v in data.values()}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    what = sys.argv[1] if len(sys.argv) > 1 else "both"
    s = Settings()
    ua = s.edgar_user_agent

    if what in ("membership", "both"):
        print(f"=== N-PORT membership: {YEARS} ===")
        rep = nport_loader.load_pit_membership(s.db_path, YEARS, ua)
        for yr in sorted(rep):
            r = rep[yr]
            print(f"  {yr}-06-30: {r['total']:>5} holdings | mapped {r['mapped']:>5} "
                  f"| UNMAPPED {r['unmapped']:>3}")

    if what in ("delistings", "both"):
        print(f"=== Form 25/15 delistings: {QUARTERS[0]}..{QUARTERS[-1]} ===")
        c2t = _cik_to_ticker(ua)
        rep2 = delisting_loader.load_delistings(s.db_path, QUARTERS, ua, cik_to_ticker=c2t)
        print(f"  total Form 25/15 rows: {sum(rep2.values())} across {len(rep2)} quarters")

    if what == "recover2022":
        print("=== 2022 delisted-name recovery (ONE pass: name-match + Form 25 CIK) ===")
        import httpx

        with httpx.Client(timeout=60.0) as c:
            ct = c.get(
                "https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": ua}
            ).json()
        rep = membership_recovery.recover_membership(s.db_path, ct)
        cov = rep["after"] / rep["total"] * 100
        print(f"  2022 coverage: {rep['before']}/{rep['total']} "
              f"({rep['before'] / rep['total'] * 100:.1f}%) -> "
              f"{rep['after']}/{rep['total']} ({cov:.1f}%)")
        print(f"  recovered: name-match {rep['recovered_sec_name']}, "
              f"Form25-CIK {rep['recovered_form25_cik']}")
        print(f"  residual unmapped: {rep['residual']} | "
              f"residual WITH 2022 insider buying: {rep['residual_with_insider_buying']} | "
              f"residual w/o CIK: {rep['residual_no_cik']}")


if __name__ == "__main__":
    main()
