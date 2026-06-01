"""
Dossier invariant sanity tests.

These assert properties that must hold for ANY ticker that passed all gates
(`status = 'CANDIDATE'`) in a run. They read the run back from DuckDB (read-only)
and would have caught both the EFTS bug and any future "GPUS-shaped" regression
(a ticker that shouldn't have survived but did).

Each test does two things:
  1. asserts the invariant HOLDS for a seeded clean candidate, and
  2. asserts the invariant CATCHES a seeded violating candidate (named offender).

A module fixture seeds a temporary DuckDB and points QIVC_DB_PATH at it, so the
checks run against "the most recent run in the DB" exactly as the live
`qivc sanity` command does — but deterministically.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from qivc.sanity import REQUIRED_GATES, check_run
from qivc.schemas import Candidate, Cluster, FilterResult, InsiderTransaction

pytestmark = pytest.mark.sanity


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _cluster(ticker: str) -> Cluster:
    txn = InsiderTransaction(
        cik="C1",
        name="Insider",
        title="CEO",
        ticker=ticker,
        shares=1000.0,
        price=100.0,
        value_usd=300_000.0,
        transaction_date=date(2026, 5, 1),
        filed_date=date(2026, 5, 1),
        transaction_code="P",
        is_director=False,
        is_officer=True,
        is_ten_percent_owner=False,
    )
    return Cluster(
        ticker=ticker,
        transactions=[txn],
        track="B",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 1),
        total_value_usd=300_000.0,
    )


def _gates(
    *,
    market_cap: float = 5e8,
    roa_failed: bool = False,
    drop_gate: str | None = None,
    unverifiable_gate: str | None = None,
) -> list[FilterResult]:
    failing = "['roa_positive']" if roa_failed else "['gross_margin_improved']"
    specs: dict[str, tuple[bool | None, float | None, str]] = {
        "regime": (True, 18.0, "risk-on"),
        "insider_conviction": (True, 1.0, "1 cluster(s) detected"),
        "f_score": (True, 8.0, f"F-Score 8/9 (PASS, threshold=7); failing: {failing}"),
        "gp_a": (True, 0.40, "GP/A 0.40 >= 0.25"),
        "valuation": (True, 14.0, "ev_ebitda within premium"),
        "revisions": (True, 0.02, "90d revision +0.02"),
        "short_interest": (True, 0.03, "SI 3% below 5%"),
        "liquidity": (True, market_cap, f"Market cap ${market_cap:,.0f}"),
    }
    if unverifiable_gate:
        old = specs[unverifiable_gate]
        specs[unverifiable_gate] = (None, old[1], "UNVERIFIABLE: missing data")
    results = [
        FilterResult(filter_name=name, passed=p, metric_value=mv, threshold=None, reason=rs)
        for name, (p, mv, rs) in specs.items()
        if name != drop_gate
    ]
    return results


def _candidate(ticker: str, **gate_kwargs: Any) -> Candidate:
    return Candidate(
        ticker=ticker,
        cluster=_cluster(ticker),
        filter_results=_gates(**gate_kwargs),
        sector="Industrials",
        gics_industry_group="Test",
        conviction_score=6,
        indicative_size_pct=0.05,
    )


def _seed(
    db_path: str,
    run_id: str,
    candidates: list[Candidate],
    classifications: list[dict[str, Any]],
) -> None:
    # a run_audit row so get_latest_run_id finds this run
    from datetime import datetime

    from qivc.storage import repositories as repo

    repo.log_node_execution(
        db_path, run_id, "apply_synthesis", datetime(2026, 6, 1), datetime(2026, 6, 1), 1.0, "ok"
    )
    repo.save_filter_results(db_path, run_id, candidates, [])
    repo.save_insider_classifications(db_path, run_id, classifications)


def _opportunistic(ticker: str, cik: str = "C1") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "cik": cik,
        "name": "Insider",
        "classification": "opportunistic",
        "years_history": 3,
        "n_purchases": 1,
        "total_value_usd": 300_000.0,
        "is_officer": True,
    }


# ---------------------------------------------------------------------------
# 1. No micro-cap survivors
# ---------------------------------------------------------------------------


def test_no_microcap_in_candidates(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    _seed(db, "run-good", [_candidate("GOOD", market_cap=5e8)], [_opportunistic("GOOD")])
    res = check_run(db, "run-good").get("no_microcap")
    assert res.passed is True, res.offenders

    _seed(db, "run-bad", [_candidate("GPUS", market_cap=9e7)], [_opportunistic("GPUS")])
    res = check_run(db, "run-bad").get("no_microcap")
    assert res.passed is False
    assert "GPUS" in res.offenders


# ---------------------------------------------------------------------------
# 2. No negative-earnings survivors (ROA component must have passed)
# ---------------------------------------------------------------------------


def test_no_negative_earnings_in_candidates(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    _seed(db, "run-good", [_candidate("GOOD", roa_failed=False)], [_opportunistic("GOOD")])
    assert check_run(db, "run-good").get("positive_earnings").passed is True

    _seed(db, "run-bad", [_candidate("NEGNI", roa_failed=True)], [_opportunistic("NEGNI")])
    res = check_run(db, "run-bad").get("positive_earnings")
    assert res.passed is False
    assert "NEGNI" in res.offenders


# ---------------------------------------------------------------------------
# 3. Candidate clusters contain only opportunistic insiders
# ---------------------------------------------------------------------------


def test_no_unclassified_insiders_in_candidate_clusters(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    _seed(db, "run-good", [_candidate("GOOD")], [_opportunistic("GOOD")])
    assert check_run(db, "run-good").get("opportunistic_clusters").passed is True

    # A candidate whose only insiders are routine/unclassified → must be flagged.
    bad_class = [
        {
            "ticker": "BADCL",
            "cik": "C1",
            "name": "Routine Guy",
            "classification": "routine",
            "years_history": 3,
            "n_purchases": 1,
            "total_value_usd": 300_000.0,
            "is_officer": True,
        },
        {
            "ticker": "BADCL",
            "cik": "C2",
            "name": "New Guy",
            "classification": "unclassified",
            "years_history": 0,
            "n_purchases": 1,
            "total_value_usd": 300_000.0,
            "is_officer": False,
        },
    ]
    _seed(db, "run-bad", [_candidate("BADCL")], bad_class)
    res = check_run(db, "run-bad").get("opportunistic_clusters")
    assert res.passed is False
    assert "BADCL" in res.offenders


# ---------------------------------------------------------------------------
# 4. All required gates evaluated to True (never None / UNVERIFIABLE)
# ---------------------------------------------------------------------------


def test_all_required_gates_evaluated_to_true(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    _seed(db, "run-good", [_candidate("GOOD")], [_opportunistic("GOOD")])
    assert check_run(db, "run-good").get("all_required_gates_true").passed is True

    # Missing a required gate → flagged.
    _seed(db, "run-missing", [_candidate("MISS", drop_gate="liquidity")], [_opportunistic("MISS")])
    res = check_run(db, "run-missing").get("all_required_gates_true")
    assert res.passed is False
    assert "MISS" in res.offenders

    # UNVERIFIABLE (passed=None) required gate → flagged.
    _seed(
        db,
        "run-unv",
        [_candidate("UNV", unverifiable_gate="valuation")],
        [_opportunistic("UNV")],
    )
    res = check_run(db, "run-unv").get("all_required_gates_true")
    assert res.passed is False
    assert "UNV" in res.offenders


# ---------------------------------------------------------------------------
# 5. Candidate has at least one Form 4 buyer
# ---------------------------------------------------------------------------


def test_candidate_has_at_least_one_form4_buyer(tmp_path: Path) -> None:
    db = str(tmp_path / "qivc.db")
    _seed(db, "run-good", [_candidate("GOOD")], [_opportunistic("GOOD")])
    assert check_run(db, "run-good").get("has_form4_buyer").passed is True

    # Candidate with no persisted insiders → fabricated candidate → flagged.
    _seed(db, "run-nobuyer", [_candidate("GHOST")], [])
    res = check_run(db, "run-nobuyer").get("has_form4_buyer")
    assert res.passed is False
    assert "GHOST" in res.offenders


# ---------------------------------------------------------------------------
# Empty / missing run behaviour
# ---------------------------------------------------------------------------


def test_no_candidates_is_vacuously_clean(tmp_path: Path) -> None:
    """A run with zero candidates passes every invariant (nothing to violate)."""
    db = str(tmp_path / "qivc.db")
    _seed(db, "run-empty", [], [])
    report = check_run(db, "run-empty")
    assert report.candidate_count == 0
    assert report.all_passed is True
    assert {r.name for r in report.results} >= set(REQUIRED_GATES_INVARIANT_NAMES)


def test_missing_db_returns_no_run(tmp_path: Path) -> None:
    """Pointing at a non-existent run returns an empty report (CLI/tests skip)."""
    db = str(tmp_path / "qivc.db")
    _seed(db, "real-run", [], [])
    report = check_run(db, "does-not-exist")
    assert report.candidate_count == 0
    assert report.all_passed is True


REQUIRED_GATES_INVARIANT_NAMES = {
    "no_microcap",
    "positive_earnings",
    "opportunistic_clusters",
    "all_required_gates_true",
    "has_form4_buyer",
}


def test_required_gates_constant_is_complete() -> None:
    assert set(REQUIRED_GATES) == {
        "regime",
        "insider_conviction",
        "f_score",
        "gp_a",
        "valuation",
        "revisions",
        "short_interest",
        "liquidity",
    }
