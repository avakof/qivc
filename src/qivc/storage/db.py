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
