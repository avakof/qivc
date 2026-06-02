"""DuckDB connection factory and schema migrations."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

_MIGRATIONS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS run_audit (
        run_id        VARCHAR     NOT NULL,
        node_name     VARCHAR     NOT NULL,
        entered_at    TIMESTAMP   NOT NULL,
        exited_at     TIMESTAMP   NOT NULL,
        duration_ms   DOUBLE      NOT NULL,
        result_summary VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS filter_results (
        run_id       VARCHAR  NOT NULL,
        ticker       VARCHAR  NOT NULL,
        status       VARCHAR  NOT NULL,   -- CANDIDATE | REJECTED
        failed_gate  VARCHAR,             -- NULL for candidates
        filter_name  VARCHAR  NOT NULL,
        passed       BOOLEAN,             -- NULL = UNVERIFIABLE
        metric_value DOUBLE,
        threshold    DOUBLE,
        reason       VARCHAR  NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS insider_classifications (
        run_id          VARCHAR  NOT NULL,
        ticker          VARCHAR  NOT NULL,
        cik             VARCHAR  NOT NULL,
        name            VARCHAR  NOT NULL,
        classification  VARCHAR  NOT NULL,   -- opportunistic | routine | unclassified
        years_history   INTEGER  NOT NULL,
        n_purchases     INTEGER  NOT NULL,   -- P-code transactions in the window
        total_value_usd DOUBLE   NOT NULL,
        is_officer      BOOLEAN  NOT NULL
    )
    """,
    # ------------------------------------------------------------------
    # Bulk Form 4 historical store (Task 8.1).
    #
    # Populated from the SEC Insider Transactions Data Sets (quarterly
    # flattened Forms 3/4/5). Holds open-market PURCHASES (code P) only.
    # One row per (transaction x reporting owner): the SEC schema links
    # NONDERIV_TRANS to REPORTINGOWNER solely via ACCESSION_NUMBER, with
    # no per-transaction owner key, so multi-owner joint filings fan out.
    # See scripts/bootstrap_bulk_data.py and BULK_DATA_NOTES.md.
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS form4_historical (
        cik                  VARCHAR  NOT NULL,
        name                 VARCHAR  NOT NULL,
        title                VARCHAR  NOT NULL,
        ticker               VARCHAR  NOT NULL,
        shares               DOUBLE   NOT NULL,
        price                DOUBLE   NOT NULL,
        value_usd            DOUBLE   NOT NULL,
        transaction_date     DATE     NOT NULL,
        filed_date           DATE     NOT NULL,
        transaction_code     VARCHAR  NOT NULL,   -- always 'P' in this table
        is_director          BOOLEAN  NOT NULL,
        is_officer           BOOLEAN  NOT NULL,
        is_ten_percent_owner BOOLEAN  NOT NULL,
        accession_number     VARCHAR  NOT NULL,   -- (cik, accession) = dedup key vs live EDGAR
        source_quarter       VARCHAR  NOT NULL    -- e.g. '2024q1'; enables idempotent re-import
    )
    """,
    # Per-quarter import ledger: makes the bootstrap idempotent and resumable,
    # and records the bulk/live cutover boundary for the daily screen.
    """
    CREATE TABLE IF NOT EXISTS bulk_load_progress (
        quarter        VARCHAR    PRIMARY KEY,   -- e.g. '2025q4'
        downloaded_at  TIMESTAMP,
        record_count   INTEGER,                  -- P-code rows imported for this quarter
        status         VARCHAR                   -- 'complete' | 'partial' | 'failed'
    )
    """,
]


def get_connection(db_path: str) -> duckdb.DuckDBPyConnection:
    """Open (or create) a DuckDB database at *db_path* and run pending migrations."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(db_path)
    _run_migrations(conn)
    return conn


def _run_migrations(conn: duckdb.DuckDBPyConnection) -> None:
    for sql in _MIGRATIONS:
        conn.execute(sql)
    conn.commit()
    log.debug("DuckDB migrations applied")
