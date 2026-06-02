"""
Bulk Form 4 historical loader (Task 8.1).

Populates the ``form4_historical`` table from the SEC **Insider Transactions
Data Sets** (https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets) —
quarterly archives of flattened Forms 3/4/5. This replaces the per-filing
EDGAR API fetch (which, capped at 100 filings, examined <1% of the universe)
with a full-coverage bulk import that downloads in minutes.

We keep **open-market purchases only** (transaction code ``P``) and apply a
strict quality filter (positive price AND positive shares) to drop the
zero-price rows the SEC dataset occasionally carries for gift-/option-adjacent
``P`` filings.

Join shape / known limitation
------------------------------
The SEC schema links the transaction table (``NONDERIV_TRANS``, keyed by
``ACCESSION_NUMBER`` + ``NONDERIV_TRANS_SK``) to the reporting-owner table
(``REPORTINGOWNER``, keyed by ``ACCESSION_NUMBER`` + ``RPTOWNERCIK``) **solely
via ``ACCESSION_NUMBER``** — there is no per-transaction owner key. For the
~2% of filings with multiple co-filing owners we therefore emit one record per
(transaction x owner): a *cross-join*. This over-counts joint filings but
cannot be avoided from the bulk data alone, and the affected rows are genuine
co-owners of the reported purchase. The live edgartools path instead attributes
all of a filing's transactions to a single chosen owner; the two diverge only
on multi-owner filings. See BULK_DATA_NOTES.md.
"""

from __future__ import annotations

import datetime as _dt
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import httpx

from qivc.schemas import InsiderTransaction
from qivc.storage.db import get_connection

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

ARCHIVE_URL_TEMPLATE = (
    "https://www.sec.gov/files/structureddata/data/"
    "insider-transactions-data-sets/{quarter}_form345.zip"
)

# TSV members we need from each quarterly archive (the other 5 are ignored).
_SUBMISSION_TSV = "SUBMISSION.tsv"
_REPORTINGOWNER_TSV = "REPORTINGOWNER.tsv"
_NONDERIV_TRANS_TSV = "NONDERIV_TRANS.tsv"

# Earliest quarter we bootstrap (PROJECT_BRIEF: 3-year historical window).
DEFAULT_START_QUARTER = "2023q1"

# Observed SEC publication lag: 2025q4 published 2026-01-07, 2026q1 published
# 2026-04-07 — both ~7 days after quarter-end. We pad to 21 days before
# *expecting* a quarter to exist, so we never enumerate an unpublished quarter.
_PUBLICATION_LAG_DAYS = 21

# The strict open-market-purchase filter, expressed once and reused for both the
# diagnostic counts and the INSERT. ``TRY_CAST(... ) > 0`` returns NULL (→ false)
# for blank/non-numeric values, so this single clause encodes all four spec
# conditions: code = 'P', price > 0, shares > 0, and both non-NULL.
_STRICT_P_FILTER = (
    "nd.TRANS_CODE = 'P' "
    "AND TRY_CAST(nd.TRANS_PRICEPERSHARE AS DOUBLE) > 0 "
    "AND TRY_CAST(nd.TRANS_SHARES AS DOUBLE) > 0"
)

# SEC bulk date columns are formatted like "28-FEB-2024".
_DATE_FMT = "%d-%b-%Y"


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FilteredSample:
    """One P-code transaction excluded by the strict filter (for reporting)."""

    transaction_date: str
    transaction_code: str
    shares: str
    price: str
    accession_number: str


@dataclass(frozen=True)
class ImportStats:
    """Outcome of importing a single quarter."""

    quarter: str
    total_p_transactions: int  # P-code rows in NONDERIV_TRANS (pre fan-out)
    kept_p_transactions: int  # P-code rows passing the strict filter (pre fan-out)
    filtered_out: int  # excluded by the strict filter
    records_inserted: int  # rows written to form4_historical (post owner fan-out)
    skipped: bool = False  # True when the quarter was already complete
    filtered_samples: list[FilteredSample] = field(default_factory=list)


@dataclass(frozen=True)
class BootstrapResult:
    """Aggregate outcome of a bootstrap/refresh run."""

    per_quarter: list[ImportStats]
    message: str = ""

    @property
    def total_records(self) -> int:
        return sum(s.records_inserted for s in self.per_quarter)


# --------------------------------------------------------------------------
# Quarter arithmetic
# --------------------------------------------------------------------------


def parse_quarter(quarter: str) -> tuple[int, int]:
    """'2024q1' -> (2024, 1). Raises ValueError on malformed input."""
    q = quarter.lower().strip()
    if "q" not in q:
        raise ValueError(f"malformed quarter: {quarter!r}")
    year_s, qnum_s = q.split("q", 1)
    year, qnum = int(year_s), int(qnum_s)
    if not 1 <= qnum <= 4:
        raise ValueError(f"quarter number out of range: {quarter!r}")
    return year, qnum


def quarter_end_date(quarter: str) -> _dt.date:
    """Last calendar day of the quarter, e.g. '2024q1' -> 2024-03-31."""
    year, qnum = parse_quarter(quarter)
    end_month = qnum * 3
    if end_month == 12:
        return _dt.date(year, 12, 31)
    # First day of the next month, minus one day.
    return _dt.date(year, end_month + 1, 1) - _dt.timedelta(days=1)


def next_quarter(quarter: str) -> str:
    year, qnum = parse_quarter(quarter)
    return f"{year + 1}q1" if qnum == 4 else f"{year}q{qnum + 1}"


def latest_expected_quarter(today: _dt.date) -> str:
    """
    Most recent quarter whose archive should be published as of *today*, using
    the observed ~7-day SEC lag plus a safety pad. We walk back from the current
    quarter until one is old enough to expect.
    """
    year = today.year
    qnum = (today.month - 1) // 3 + 1
    candidate = f"{year}q{qnum}"
    while quarter_end_date(candidate) + _dt.timedelta(days=_PUBLICATION_LAG_DAYS) > today:
        # Step back one quarter.
        y, q = parse_quarter(candidate)
        candidate = f"{y - 1}q4" if q == 1 else f"{y}q{q - 1}"
    return candidate


def quarters_in_range(start: str, end: str) -> list[str]:
    """Inclusive list of quarters from *start* to *end* in chronological order."""
    out: list[str] = []
    cur = start
    end_end = quarter_end_date(end)
    while quarter_end_date(cur) <= end_end:
        out.append(cur)
        if cur == end:
            break
        cur = next_quarter(cur)
    return out


def archive_url(quarter: str) -> str:
    return ARCHIVE_URL_TEMPLATE.format(quarter=quarter)


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------


def archive_exists(quarter: str, user_agent: str, client: httpx.Client | None = None) -> bool:
    """True if the SEC archive for *quarter* is published (HTTP 200/206)."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        resp = client.get(
            archive_url(quarter),
            headers={"User-Agent": user_agent, "Range": "bytes=0-0"},
        )
        return resp.status_code in (200, 206)
    except httpx.HTTPError as exc:
        log.warning("archive_exists check failed for %s: %s", quarter, exc)
        return False
    finally:
        if owns_client:
            client.close()


def download_archive(
    quarter: str,
    cache_dir: Path,
    user_agent: str,
    client: httpx.Client | None = None,
) -> Path:
    """
    Download the quarterly archive into *cache_dir*, returning the local path.

    Idempotent: a fully-downloaded archive is reused. The download streams to a
    ``.part`` file and is atomically renamed on success, so an interrupted run
    never leaves a truncated archive that looks complete.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / f"{quarter}_form345.zip"
    if dest.exists() and dest.stat().st_size > 0:
        log.info("archive %s already cached (%d bytes)", quarter, dest.stat().st_size)
        return dest

    owns_client = client is None
    client = client or httpx.Client(timeout=120.0, follow_redirects=True)
    part = dest.with_suffix(".zip.part")
    try:
        with client.stream("GET", archive_url(quarter), headers={"User-Agent": user_agent}) as resp:
            resp.raise_for_status()
            with part.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 16):
                    fh.write(chunk)
        part.replace(dest)
        log.info("downloaded archive %s (%d bytes)", quarter, dest.stat().st_size)
        return dest
    finally:
        part.unlink(missing_ok=True)
        if owns_client:
            client.close()


def _extract_members(zip_path: Path, dest_dir: Path) -> tuple[Path, Path, Path]:
    """Extract the three TSVs we need; return (submission, owner, trans) paths."""
    wanted = (_SUBMISSION_TSV, _REPORTINGOWNER_TSV, _NONDERIV_TRANS_TSV)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for member in wanted:
            if member not in names:
                raise ValueError(f"{zip_path.name} is missing expected member {member!r}")
            zf.extract(member, dest_dir)
    return (
        dest_dir / _SUBMISSION_TSV,
        dest_dir / _REPORTINGOWNER_TSV,
        dest_dir / _NONDERIV_TRANS_TSV,
    )


# --------------------------------------------------------------------------
# Import (DuckDB SQL join + filter)
# --------------------------------------------------------------------------


def _read_csv(path: Path) -> str:
    """DuckDB read_csv expression: tab-delimited, header row, everything as text."""
    return (
        f"read_csv('{path.as_posix()}', delim='\\t', header=true, all_varchar=true, sample_size=-1)"
    )


def import_quarter(
    conn: duckdb.DuckDBPyConnection,
    submission_path: Path,
    owner_path: Path,
    trans_path: Path,
    quarter: str,
) -> ImportStats:
    """
    Join the three TSVs, filter to strict open-market purchases, and insert into
    ``form4_historical``. Existing rows for *quarter* are deleted first, so a
    re-import is idempotent (no duplicate rows).
    """
    sub, own, nd = _read_csv(submission_path), _read_csv(owner_path), _read_csv(trans_path)

    # Diagnostic counts at the transaction level (before owner fan-out).
    total_p = conn.execute(f"SELECT count(*) FROM {nd} nd WHERE nd.TRANS_CODE = 'P'").fetchone()
    kept_p = conn.execute(f"SELECT count(*) FROM {nd} nd WHERE {_STRICT_P_FILTER}").fetchone()
    total_p_n = int(total_p[0]) if total_p else 0
    kept_p_n = int(kept_p[0]) if kept_p else 0

    # A sample of the rows the strict filter removes, so the filter can be
    # eyeballed for over-aggressiveness. COALESCE makes the positivity tests
    # non-NULL booleans before negating, so blank/non-numeric rows (where
    # TRY_CAST -> NULL) are correctly counted as excluded under SQL's
    # three-valued logic (NOT NULL would otherwise drop them from the sample).
    sample_rows = conn.execute(
        f"""
        SELECT nd.TRANS_DATE, nd.TRANS_CODE, nd.TRANS_SHARES,
               nd.TRANS_PRICEPERSHARE, nd.ACCESSION_NUMBER
        FROM {nd} nd
        WHERE nd.TRANS_CODE = 'P'
          AND NOT (
              COALESCE(TRY_CAST(nd.TRANS_PRICEPERSHARE AS DOUBLE) > 0, FALSE)
              AND COALESCE(TRY_CAST(nd.TRANS_SHARES AS DOUBLE) > 0, FALSE)
          )
        LIMIT 5
        """
    ).fetchall()
    samples = [
        FilteredSample(
            transaction_date=str(r[0]),
            transaction_code=str(r[1]),
            shares=str(r[2]),
            price=str(r[3]),
            accession_number=str(r[4]),
        )
        for r in sample_rows
    ]

    # Idempotent re-import: clear any prior rows for this quarter.
    conn.execute("DELETE FROM form4_historical WHERE source_quarter = ?", [quarter])

    # The fan-out join: one row per (transaction x reporting owner). See module
    # docstring for why this cross-join is unavoidable from the bulk schema.
    conn.execute(
        f"""
        INSERT INTO form4_historical (
            cik, name, title, ticker, shares, price, value_usd,
            transaction_date, filed_date, transaction_code,
            is_director, is_officer, is_ten_percent_owner,
            accession_number, source_quarter
        )
        SELECT
            o.RPTOWNERCIK,
            COALESCE(o.RPTOWNERNAME, ''),
            COALESCE(o.RPTOWNER_TITLE, ''),
            COALESCE(s.ISSUERTRADINGSYMBOL, ''),
            CAST(nd.TRANS_SHARES AS DOUBLE),
            CAST(nd.TRANS_PRICEPERSHARE AS DOUBLE),
            CAST(nd.TRANS_SHARES AS DOUBLE) * CAST(nd.TRANS_PRICEPERSHARE AS DOUBLE),
            strptime(nd.TRANS_DATE, '{_DATE_FMT}')::DATE,
            strptime(s.FILING_DATE, '{_DATE_FMT}')::DATE,
            'P',
            contains(COALESCE(o.RPTOWNER_RELATIONSHIP, ''), 'Director'),
            contains(COALESCE(o.RPTOWNER_RELATIONSHIP, ''), 'Officer'),
            contains(COALESCE(o.RPTOWNER_RELATIONSHIP, ''), 'TenPercentOwner'),
            nd.ACCESSION_NUMBER,
            ?
        FROM {nd} nd
        JOIN {sub} s ON nd.ACCESSION_NUMBER = s.ACCESSION_NUMBER
        JOIN {own} o ON nd.ACCESSION_NUMBER = o.ACCESSION_NUMBER
        WHERE {_STRICT_P_FILTER}
        """,
        [quarter],
    )
    inserted = conn.execute(
        "SELECT count(*) FROM form4_historical WHERE source_quarter = ?", [quarter]
    ).fetchone()
    inserted_n = int(inserted[0]) if inserted else 0
    conn.commit()

    return ImportStats(
        quarter=quarter,
        total_p_transactions=total_p_n,
        kept_p_transactions=kept_p_n,
        filtered_out=total_p_n - kept_p_n,
        records_inserted=inserted_n,
        filtered_samples=samples,
    )


def _record_progress(
    conn: duckdb.DuckDBPyConnection,
    quarter: str,
    record_count: int,
    status: str,
    now: _dt.datetime,
) -> None:
    conn.execute("DELETE FROM bulk_load_progress WHERE quarter = ?", [quarter])
    conn.execute(
        """
        INSERT INTO bulk_load_progress (quarter, downloaded_at, record_count, status)
        VALUES (?, ?, ?, ?)
        """,
        [quarter, now, record_count, status],
    )
    conn.commit()


def _is_complete(conn: duckdb.DuckDBPyConnection, quarter: str) -> bool:
    row = conn.execute(
        "SELECT status FROM bulk_load_progress WHERE quarter = ?", [quarter]
    ).fetchone()
    return row is not None and str(row[0]) == "complete"


def load_quarter(
    conn: duckdb.DuckDBPyConnection,
    quarter: str,
    cache_dir: Path,
    user_agent: str,
    now: _dt.datetime,
    client: httpx.Client | None = None,
    force: bool = False,
) -> ImportStats:
    """Download (if needed), import, and mark a single quarter complete."""
    if not force and _is_complete(conn, quarter):
        log.info("quarter %s already complete; skipping", quarter)
        return ImportStats(quarter, 0, 0, 0, 0, skipped=True)

    zip_path = download_archive(quarter, cache_dir, user_agent, client=client)
    extract_dir = cache_dir / f"_extract_{quarter}"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        sub_p, own_p, nd_p = _extract_members(zip_path, extract_dir)
        try:
            stats = import_quarter(conn, sub_p, own_p, nd_p, quarter)
        except Exception:
            _record_progress(conn, quarter, 0, "failed", now)
            raise
        _record_progress(conn, quarter, stats.records_inserted, "complete", now)
        return stats
    finally:
        for member in (_SUBMISSION_TSV, _REPORTINGOWNER_TSV, _NONDERIV_TRANS_TSV):
            (extract_dir / member).unlink(missing_ok=True)
        extract_dir.rmdir() if extract_dir.exists() else None


# --------------------------------------------------------------------------
# Top-level entry points
# --------------------------------------------------------------------------


def bootstrap(
    db_path: str,
    user_agent: str,
    cache_dir: Path,
    now: _dt.datetime,
    start_quarter: str = DEFAULT_START_QUARTER,
    end_quarter: str | None = None,
    force: bool = False,
    client: httpx.Client | None = None,
) -> BootstrapResult:
    """
    Bootstrap the historical store from *start_quarter* to the most recently
    published quarter (or *end_quarter* if given). Idempotent and resumable:
    quarters already marked complete are skipped unless *force* is set.
    """
    end = end_quarter or latest_expected_quarter(now.date())
    quarters = quarters_in_range(start_quarter, end)
    log.info("bootstrap %d quarters: %s .. %s", len(quarters), start_quarter, end)

    owns_client = client is None
    client = client or httpx.Client(timeout=120.0, follow_redirects=True)
    stats: list[ImportStats] = []
    try:
        with get_connection(db_path) as conn:
            for q in quarters:
                stats.append(
                    load_quarter(conn, q, cache_dir, user_agent, now, client=client, force=force)
                )
    finally:
        if owns_client:
            client.close()
    return BootstrapResult(per_quarter=stats)


def refresh(
    db_path: str,
    user_agent: str,
    cache_dir: Path,
    now: _dt.datetime,
    client: httpx.Client | None = None,
) -> BootstrapResult:
    """
    Idempotent weekly refresh: load the next quarter after the latest one in
    ``bulk_load_progress`` *iff* its archive has been published. Otherwise do
    nothing and report when the next update is expected. Safe to cron.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=120.0, follow_redirects=True)
    try:
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT quarter FROM bulk_load_progress WHERE status = 'complete' "
                "ORDER BY quarter DESC LIMIT 1"
            ).fetchall()
            # String DESC on 'YYYYqN' sorts chronologically for same-century data.
            latest = max((str(r[0]) for r in row), key=_quarter_sort_key) if row else None

            if latest is None:
                return BootstrapResult(
                    per_quarter=[],
                    message=(
                        "No quarters loaded yet; run a full bootstrap first "
                        "(python scripts/bootstrap_bulk_data.py)."
                    ),
                )

            target = next_quarter(latest)
            if not archive_exists(target, user_agent, client=client):
                expected = quarter_end_date(target) + _dt.timedelta(days=_PUBLICATION_LAG_DAYS)
                return BootstrapResult(
                    per_quarter=[],
                    message=(
                        f"Latest loaded quarter is {latest}; next quarter {target} "
                        f"is not yet published. Next bulk update expected after "
                        f"{expected.isoformat()}; no action taken."
                    ),
                )

            stat = load_quarter(conn, target, cache_dir, user_agent, now, client=client)
            return BootstrapResult(
                per_quarter=[stat],
                message=f"Loaded new quarter {target}: {stat.records_inserted} records.",
            )
    finally:
        if owns_client:
            client.close()


def _quarter_sort_key(quarter: str) -> tuple[int, int]:
    return parse_quarter(quarter)


# --------------------------------------------------------------------------
# Validation queries
# --------------------------------------------------------------------------


def validate(db_path: str) -> dict[str, object]:
    """Return summary stats over ``form4_historical`` for post-load reporting."""
    with get_connection(db_path) as conn:
        total = conn.execute("SELECT count(*) FROM form4_historical").fetchone()
        date_range = conn.execute(
            "SELECT min(filed_date), max(filed_date) FROM form4_historical"
        ).fetchone()
        per_year = conn.execute(
            """
            SELECT year(filed_date) AS yr, count(*) AS n
            FROM form4_historical
            GROUP BY yr ORDER BY yr
            """
        ).fetchall()
        distinct_tickers = conn.execute(
            "SELECT count(DISTINCT ticker) FROM form4_historical"
        ).fetchone()
        progress = conn.execute(
            "SELECT quarter, record_count, status FROM bulk_load_progress ORDER BY quarter"
        ).fetchall()
    return {
        "total_records": int(total[0]) if total else 0,
        "min_filed_date": str(date_range[0]) if date_range and date_range[0] else None,
        "max_filed_date": str(date_range[1]) if date_range and date_range[1] else None,
        "distinct_tickers": int(distinct_tickers[0]) if distinct_tickers else 0,
        "per_year": [(int(r[0]), int(r[1])) for r in per_year],
        "quarters_loaded": [(str(r[0]), int(r[1]), str(r[2])) for r in progress],
    }


# --------------------------------------------------------------------------
# Read API — serve the daily scan window from the bulk store
# --------------------------------------------------------------------------


def bulk_cutover_date(db_path: str) -> _dt.date | None:
    """
    The bulk/live boundary: the end date of the most recent **complete** quarter
    in the store. The bulk store is authoritative for filings filed on or before
    this date; anything filed after it must come from live EDGAR (the daily
    delta). Returns ``None`` if no quarter is loaded.

    Quarter-end (not max(filed_date)) is the principled boundary because SEC
    assigns each filing to a quarter by its *filing* date, so a complete quarter
    archive covers every filing filed through its quarter-end.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT quarter FROM bulk_load_progress WHERE status = 'complete'"
        ).fetchall()
    quarters = [str(r[0]) for r in rows]
    if not quarters:
        return None
    return quarter_end_date(max(quarters, key=_quarter_sort_key))


def earliest_filed_date(db_path: str) -> _dt.date | None:
    """Earliest ``filed_date`` in the store, or ``None`` if empty. Used to decide
    whether the bulk store reaches far enough back to cover a CMP lookback."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT min(filed_date) FROM form4_historical").fetchone()
    if not row or row[0] is None:
        return None
    val = row[0]
    return val if isinstance(val, _dt.date) else _dt.date.fromisoformat(str(val)[:10])


def read_purchases_for_cik(
    db_path: str,
    cik: str,
    start_date: _dt.date,
    end_date: _dt.date,
) -> list[InsiderTransaction]:
    """
    Read one insider's open-market purchases (filed in ``[start_date, end_date]``)
    for CMP history. Matches the CIK as stored and zero-stripped, since the bulk
    store keeps SEC's zero-padded CIKs while callers may pass either form.
    """
    cik_stripped = cik.lstrip("0") or "0"
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT cik, name, title, ticker, shares, price, value_usd,
                   transaction_date, filed_date, transaction_code,
                   is_director, is_officer, is_ten_percent_owner, accession_number
            FROM form4_historical
            WHERE filed_date >= ? AND filed_date <= ?
              AND ltrim(cik, '0') = ?
            ORDER BY filed_date
            """,
            [start_date, end_date, cik_stripped],
        ).fetchall()
    return [_row_to_transaction(r) for r in rows]


def _row_to_transaction(r: tuple[object, ...]) -> InsiderTransaction:
    return InsiderTransaction(
        cik=str(r[0]),
        name=str(r[1]),
        title=str(r[2]),
        ticker=str(r[3]),
        shares=float(r[4]),  # type: ignore[arg-type]
        price=float(r[5]),  # type: ignore[arg-type]
        value_usd=float(r[6]),  # type: ignore[arg-type]
        transaction_date=r[7],
        filed_date=r[8],
        transaction_code=str(r[9]),
        is_director=bool(r[10]),
        is_officer=bool(r[11]),
        is_ten_percent_owner=bool(r[12]),
        accession_number=str(r[13]),
    )


def read_purchases(
    db_path: str,
    start_date: _dt.date,
    end_date: _dt.date,
    ticker: str | None = None,
) -> list[InsiderTransaction]:
    """
    Read open-market purchases from ``form4_historical`` for filings filed in
    ``[start_date, end_date]`` (inclusive), optionally scoped to *ticker*.

    Returns the **full multi-owner fan-out** (one record per transaction x owner),
    NOT de-duplicated. Cluster detection de-duplicates by accession downstream
    (see ``cluster_detector.dedupe_by_accession``); the audit / classifications
    path consumes the full detail. See BULK_DATA_NOTES.md and STRATEGY_NOTES.md
    ("Fan-Out Dedup Policy").
    """
    params: list[object] = [start_date, end_date]
    ticker_clause = ""
    if ticker:
        ticker_clause = "AND ticker = ?"
        params.append(ticker)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT cik, name, title, ticker, shares, price, value_usd,
                   transaction_date, filed_date, transaction_code,
                   is_director, is_officer, is_ten_percent_owner, accession_number
            FROM form4_historical
            WHERE filed_date >= ? AND filed_date <= ? {ticker_clause}
            ORDER BY filed_date, accession_number
            """,
            params,
        ).fetchall()
    return [_row_to_transaction(r) for r in rows]
