"""
Repository functions — CRUD operations on the run_audit table.

The module is intentionally stateless: each call opens a fresh DuckDB
connection so callers (including decorator-wrapped nodes running in
different threads) can safely call it without sharing connection objects.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from qivc.storage.db import get_connection

if TYPE_CHECKING:
    from qivc.schemas import Candidate, RejectedCandidate

log = logging.getLogger(__name__)


def log_node_execution(
    db_path: str,
    run_id: str,
    node_name: str,
    entered_at: datetime,
    exited_at: datetime,
    duration_ms: float,
    result_summary: str = "",
) -> None:
    """Insert one row into run_audit for a completed node execution."""
    try:
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO run_audit
                    (run_id, node_name, entered_at, exited_at, duration_ms, result_summary)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [run_id, node_name, entered_at, exited_at, duration_ms, result_summary],
            )
    except Exception as exc:
        log.warning("Failed to log node execution to run_audit: %s", exc)


def get_run_audit(db_path: str, run_id: str) -> list[dict[str, object]]:
    """Return all audit rows for a given run_id, ordered by entered_at."""
    with get_connection(db_path) as conn:
        result = conn.execute(
            """
            SELECT run_id, node_name, entered_at, exited_at, duration_ms, result_summary
            FROM run_audit
            WHERE run_id = ?
            ORDER BY entered_at
            """,
            [run_id],
        ).fetchall()
    cols = ["run_id", "node_name", "entered_at", "exited_at", "duration_ms", "result_summary"]
    return [dict(zip(cols, row, strict=False)) for row in result]


def save_filter_results(
    db_path: str,
    run_id: str,
    candidates: list[Candidate],
    rejected: list[RejectedCandidate],
) -> None:
    """Persist per-ticker per-gate filter results for later `qivc audit`."""
    rows: list[list[object]] = []
    for c in candidates:
        for fr in c.filter_results:
            rows.append(
                [
                    run_id,
                    c.ticker,
                    "CANDIDATE",
                    None,
                    fr.filter_name,
                    fr.passed,
                    fr.metric_value,
                    fr.threshold,
                    fr.reason,
                ]
            )
    for r in rejected:
        for fr in r.filter_results:
            rows.append(
                [
                    run_id,
                    r.ticker,
                    "REJECTED",
                    r.failed_gate,
                    fr.filter_name,
                    fr.passed,
                    fr.metric_value,
                    fr.threshold,
                    fr.reason,
                ]
            )
    if not rows:
        return
    try:
        with get_connection(db_path) as conn:
            conn.executemany(
                """
                INSERT INTO filter_results
                    (run_id, ticker, status, failed_gate, filter_name,
                     passed, metric_value, threshold, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
    except Exception as exc:
        log.warning("Failed to persist filter_results: %s", exc)


def get_filter_results(db_path: str, run_id: str) -> list[dict[str, object]]:
    """Return all persisted filter_results rows for a run, ordered by ticker."""
    with get_connection(db_path) as conn:
        result = conn.execute(
            """
            SELECT run_id, ticker, status, failed_gate, filter_name,
                   passed, metric_value, threshold, reason
            FROM filter_results
            WHERE run_id = ?
            ORDER BY ticker, filter_name
            """,
            [run_id],
        ).fetchall()
    cols = [
        "run_id",
        "ticker",
        "status",
        "failed_gate",
        "filter_name",
        "passed",
        "metric_value",
        "threshold",
        "reason",
    ]
    return [dict(zip(cols, row, strict=False)) for row in result]
