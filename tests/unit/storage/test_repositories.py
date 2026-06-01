"""Unit tests for the storage repositories (run_audit + filter_results)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qivc.schemas import Candidate, Cluster, FilterResult, InsiderTransaction, RejectedCandidate
from qivc.storage import repositories as repo


def _txn() -> InsiderTransaction:
    from datetime import date

    return InsiderTransaction(
        cik="1",
        name="X",
        title="CEO",
        ticker="UNH",
        shares=1.0,
        price=1.0,
        value_usd=1.0,
        transaction_date=date(2026, 5, 1),
        filed_date=date(2026, 5, 1),
        transaction_code="P",
        is_director=False,
        is_officer=True,
        is_ten_percent_owner=False,
    )


def _cluster() -> Cluster:
    from datetime import date

    return Cluster(
        ticker="UNH",
        transactions=[_txn()],
        track="B",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 1),
        total_value_usd=1.0,
    )


def test_log_and_get_run_audit(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    now = datetime(2026, 5, 1, 12, 0, 0)
    repo.log_node_execution(db, "run-1", "regime_check", now, now, 1.5, "ok")
    rows = repo.get_run_audit(db, "run-1")
    assert len(rows) == 1
    assert rows[0]["node_name"] == "regime_check"
    assert rows[0]["duration_ms"] == 1.5


def test_get_run_audit_empty(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    assert repo.get_run_audit(db, "missing") == []


def test_save_and_get_filter_results(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    candidate = Candidate(
        ticker="UNH",
        cluster=_cluster(),
        filter_results=[
            FilterResult(
                filter_name="f_score",
                passed=True,
                metric_value=8.0,
                threshold=7.0,
                reason="F-Score 8/9",
            ),
        ],
    )
    rejected = RejectedCandidate(
        ticker="XYZ",
        failed_gate="gp_a",
        reason="too low",
        filter_results=[
            FilterResult(
                filter_name="gp_a",
                passed=False,
                metric_value=0.1,
                threshold=0.25,
                reason="GP/A 0.1 < 0.25",
            ),
        ],
    )
    repo.save_filter_results(db, "run-2", [candidate], [rejected])

    rows = repo.get_filter_results(db, "run-2")
    assert len(rows) == 2
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["UNH"]["status"] == "CANDIDATE"
    assert by_ticker["UNH"]["passed"] is True
    assert by_ticker["XYZ"]["status"] == "REJECTED"
    assert by_ticker["XYZ"]["failed_gate"] == "gp_a"
    assert by_ticker["XYZ"]["passed"] is False


def test_save_filter_results_empty_is_noop(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    repo.save_filter_results(db, "run-3", [], [])
    assert repo.get_filter_results(db, "run-3") == []


def test_log_node_execution_bad_path_is_swallowed() -> None:
    """A bad DB path must not raise (audit logging is best-effort)."""
    now = datetime(2026, 5, 1)
    # Directory path that cannot be a DB file → exception caught internally
    repo.log_node_execution("/nonexistent\x00/bad.db", "n", "node", now, now, 1.0, "x")
