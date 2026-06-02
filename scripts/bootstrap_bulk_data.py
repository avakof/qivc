#!/usr/bin/env python
"""
Bootstrap the historical Form 4 store from the SEC Insider Transactions Data Sets.

One-time setup (downloads ~13 MB/quarter, a few minutes total)::

    uv run python scripts/bootstrap_bulk_data.py

Weekly maintenance (idempotent; loads the next quarter only if published)::

    uv run python scripts/bootstrap_bulk_data.py --refresh

The run is resumable: quarters already marked ``complete`` in ``bulk_load_progress``
are skipped, so a killed run can simply be restarted.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from qivc.config import Settings
from qivc.data import bulk_loader
from qivc.data.bulk_loader import BootstrapResult
from qivc.logging_config import configure_logging


def _print_report(result: BootstrapResult, db_path: str) -> None:
    if result.message:
        print(result.message)

    for s in result.per_quarter:
        if s.skipped:
            print(f"  {s.quarter}: skipped (already complete)")
            continue
        print(
            f"  {s.quarter}: {s.records_inserted:,} records inserted "
            f"({s.kept_p_transactions:,}/{s.total_p_transactions:,} P-transactions kept, "
            f"{s.filtered_out:,} filtered out)"
        )
        if s.filtered_out and s.filtered_samples:
            print(
                f"    sample of {len(s.filtered_samples)} filtered-out P rows "
                "(zero/blank price or shares):"
            )
            for fs in s.filtered_samples:
                print(
                    f"      date={fs.transaction_date} code={fs.transaction_code} "
                    f"shares=[{fs.shares}] price=[{fs.price}] acc={fs.accession_number}"
                )

    print()
    v = bulk_loader.validate(db_path)
    print("form4_historical validation:")
    print(f"  total records:    {v['total_records']:,}")
    print(f"  distinct tickers: {v['distinct_tickers']:,}")
    print(f"  filed_date range: {v['min_filed_date']} .. {v['max_filed_date']}")
    print("  per-year breakdown (by filed_date):")
    for yr, n in v["per_year"]:  # type: ignore[union-attr]
        print(f"    {yr}: {n:,}")
    print("  quarters loaded:")
    for q, n, status in v["quarters_loaded"]:  # type: ignore[union-attr]
        print(f"    {q}: {n:,} ({status})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Load only the next unpublished quarter if available; idempotent (cron-safe).",
    )
    parser.add_argument(
        "--start-quarter",
        default=bulk_loader.DEFAULT_START_QUARTER,
        help=f"First quarter to load (default {bulk_loader.DEFAULT_START_QUARTER}).",
    )
    parser.add_argument(
        "--end-quarter",
        default=None,
        help="Last quarter to load (default: most recently published quarter).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-import quarters even if already marked complete.",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/bulk_cache",
        help="Directory for downloaded archives (default data/bulk_cache).",
    )
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings.log_level, settings.log_format)
    cache_dir = Path(args.cache_dir)
    now = datetime.now(UTC)

    if args.refresh:
        result = bulk_loader.refresh(settings.db_path, settings.edgar_user_agent, cache_dir, now)
    else:
        result = bulk_loader.bootstrap(
            settings.db_path,
            settings.edgar_user_agent,
            cache_dir,
            now,
            start_quarter=args.start_quarter,
            end_quarter=args.end_quarter,
            force=args.force,
        )

    _print_report(result, settings.db_path)


if __name__ == "__main__":
    main()
