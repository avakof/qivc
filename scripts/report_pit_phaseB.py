#!/usr/bin/env python
"""
Task 14 Phase B acceptance report (read-only): per-year membership mapped/unmapped,
delisting spot-checks, and the universe diff vs the biased snapshot.

    uv run python scripts/report_pit_phaseB.py
"""

from __future__ import annotations

import datetime as dt

from qivc.backtest.pit_universe import SecPitProvider
from qivc.backtest.universe import iwm_tickers
from qivc.config import Settings
from qivc.storage.db import get_connection

_SPOT = ["REVLON", "TUPPERWARE", "CANO HEALTH", "CALLON"]


def main() -> None:
    s = Settings()
    db = s.db_path
    with get_connection(db) as conn:
        print("=== iwm_pit_constituents: per-year mapped / unmapped ===")
        rows = conn.execute(
            """SELECT as_of_date, count(*) tot,
                      sum(CASE WHEN mapped_flag THEN 1 ELSE 0 END) mapped,
                      sum(CASE WHEN mapped_flag THEN 0 ELSE 1 END) unmapped,
                      min(filed_date) filed
               FROM iwm_pit_constituents GROUP BY as_of_date ORDER BY as_of_date"""
        ).fetchall()
        for as_of, tot, mapped, unmapped, filed in rows:
            pct = mapped / tot * 100 if tot else 0
            print(f"  {as_of} (filed {filed}): {tot:>5} holdings | mapped {mapped:>5} "
                  f"({pct:4.1f}%) | UNMAPPED {unmapped:>3}")

        print("\n=== unmapped names sample (flagged, NOT dropped) — 2022 ===")
        u = conn.execute(
            """SELECT name, cusip, isin FROM iwm_pit_constituents
               WHERE as_of_date = DATE '2022-06-30' AND NOT mapped_flag LIMIT 12"""
        ).fetchall()
        for n, c, i in u:
            print(f"    {n[:40]:<42} cusip={c} isin={i}")

        print("\n=== delistings_historical: total + by form ===")
        tot = conn.execute("SELECT count(*) FROM delistings_historical").fetchone()[0]
        byform = conn.execute(
            "SELECT form_type, count(*) FROM delistings_historical "
            "GROUP BY form_type ORDER BY 2 DESC"
        ).fetchall()
        print(f"  total rows: {tot}")
        print("  by form:", ", ".join(f"{f}={n}" for f, n in byform))

        print("\n=== delisting spot-checks (known 2022 events) ===")
        for name in _SPOT:
            hits = conn.execute(
                """SELECT company_name, form_type, filed_date FROM delistings_historical
                   WHERE upper(company_name) LIKE ? ORDER BY filed_date""",
                [f"%{name}%"],
            ).fetchall()
            tag = "FOUND" if hits else "MISSING"
            print(f"  [{tag}] {name}: " + "; ".join(
                f"{h[0]} {h[1]} {h[2]}" for h in hits[:3]))

    # Universe diff vs the biased snapshot
    print("\n=== PIT universe vs biased current snapshot ===")
    sec = SecPitProvider(db)
    biased = set(iwm_tickers())
    print(f"  biased snapshot (current IWM): {len(biased)} tickers")
    for yr in (2022, 2025):
        # as_of after that year's N-PORT filing so the year's snapshot is in force
        u = sec.get_constituents(dt.date(yr, 9, 15))
        added_back = u - biased   # in the PIT year-universe but gone from today's snapshot
        dropped_now = biased - u  # in today's snapshot but not in that PIT year
        print(f"  {yr}: PIT universe = {len(u)} tickers | "
              f"in {yr}-but-not-today (survivor-bias names restored) = {len(added_back)} | "
              f"in-today-but-not-{yr} = {len(dropped_now)}")


if __name__ == "__main__":
    main()
