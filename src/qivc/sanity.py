"""
Dossier invariant sanity checks.

Read-only properties that must hold for every ticker that passed all gates
(`status = 'CANDIDATE'`) in a run. They run post-screen against the persisted
DuckDB tables (`filter_results`, `insider_classifications`) — they surface
anomalies (a survivor that shouldn't have survived) but do not gate output.

A run with zero candidates passes every invariant vacuously.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from qivc.storage import repositories as repo

# Gates that every survivor must have passed (passed=True, not None/UNVERIFIABLE).
REQUIRED_GATES: list[str] = [
    "regime",
    "insider_conviction",
    "f_score",
    "gp_a",
    "valuation",
    "revisions",
    "short_interest",
    "liquidity",
]

_MIN_MARKET_CAP_USD = 300_000_000.0


@dataclass(frozen=True)
class SanityResult:
    name: str
    passed: bool
    detail: str
    offenders: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunSnapshot:
    """Minimal view of a completed run for status/consistency checks."""

    run_id: str
    candidates: list[str]
    status: str  # "completed" | "errored" | "unknown"


def derive_run_status(db_path: str, run_id: str) -> str:
    """
    Derive a run's status from run_audit (no separate status column is persisted):
      - "completed" if apply_synthesis logged a non-error summary,
      - "errored"   if any node logged an ERROR summary,
      - "unknown"   otherwise (e.g. process killed before the final node).
    """
    rows = repo.get_run_audit(db_path, run_id)
    if not rows:
        return "unknown"
    synth_ok = any(
        r["node_name"] == "apply_synthesis" and not str(r["result_summary"]).startswith("ERROR")
        for r in rows
    )
    if synth_ok:
        return "completed"
    if any(str(r["result_summary"]).startswith("ERROR") for r in rows):
        return "errored"
    return "unknown"


def load_run(db_path: str, run_id: str | None = None) -> RunSnapshot | None:
    """Load a RunSnapshot (default: most recent run). None if the DB has no runs."""
    if run_id is None:
        run_id = repo.get_latest_run_id(db_path)
    if run_id is None:
        return None
    candidate_gates, _ = _load(db_path, run_id)
    return RunSnapshot(
        run_id=run_id,
        candidates=sorted(candidate_gates.keys()),
        status=derive_run_status(db_path, run_id),
    )


@dataclass(frozen=True)
class SanityReport:
    run_id: str | None
    candidate_count: int
    results: list[SanityResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def get(self, name: str) -> SanityResult:
        for r in self.results:
            if r.name == name:
                return r
        raise KeyError(name)


def _load(
    db_path: str, run_id: str
) -> tuple[
    dict[str, dict[str, dict[str, object]]],
    dict[str, list[dict[str, object]]],
]:
    """
    Return (candidate_gates, classifications_by_ticker).

    candidate_gates: {ticker: {filter_name: row}} for status='CANDIDATE' only.
    classifications_by_ticker: {ticker: [classification rows]}.
    """
    candidate_gates: dict[str, dict[str, dict[str, object]]] = {}
    for row in repo.get_filter_results(db_path, run_id):
        if row["status"] != "CANDIDATE":
            continue
        ticker = str(row["ticker"])
        candidate_gates.setdefault(ticker, {})[str(row["filter_name"])] = row

    classifications: dict[str, list[dict[str, object]]] = {}
    for row in repo.get_insider_classifications(db_path, run_id):
        classifications.setdefault(str(row["ticker"]), []).append(row)

    return candidate_gates, classifications


# ---------------------------------------------------------------------------
# Individual invariants — each returns (passed, offenders)
# ---------------------------------------------------------------------------


def _check_no_microcap(
    candidate_gates: dict[str, dict[str, dict[str, object]]],
) -> SanityResult:
    offenders = []
    for ticker, gates in candidate_gates.items():
        liq = gates.get("liquidity")
        mv = liq["metric_value"] if liq else None
        if isinstance(mv, (int, float)) and float(mv) < _MIN_MARKET_CAP_USD:
            offenders.append(ticker)
    return SanityResult(
        name="no_microcap",
        passed=not offenders,
        detail=f"every candidate market cap >= ${_MIN_MARKET_CAP_USD:,.0f}",
        offenders=offenders,
    )


def _check_positive_earnings(
    candidate_gates: dict[str, dict[str, dict[str, object]]],
) -> SanityResult:
    # F-Score component 1 (roa_positive) failing → negative ROA → non-positive NI.
    # The f_score reason lists the failing components, so we detect it there.
    offenders = []
    for ticker, gates in candidate_gates.items():
        fscore = gates.get("f_score")
        if fscore and "roa_positive" in str(fscore["reason"]):
            offenders.append(ticker)
    return SanityResult(
        name="positive_earnings",
        passed=not offenders,
        detail="every candidate has positive ROA (F-Score roa_positive component)",
        offenders=offenders,
    )


def _check_opportunistic_clusters(
    candidate_gates: dict[str, dict[str, dict[str, object]]],
    classifications: dict[str, list[dict[str, object]]],
) -> SanityResult:
    # A candidate's cluster is built only from opportunistic insiders; assert each
    # candidate ticker has >= 1 opportunistic insider (and is not relying solely on
    # routine/unclassified filers).
    offenders = []
    for ticker in candidate_gates:
        rows = classifications.get(ticker, [])
        opportunistic = [r for r in rows if r["classification"] == "opportunistic"]
        if not opportunistic:
            offenders.append(ticker)
    return SanityResult(
        name="opportunistic_clusters",
        passed=not offenders,
        detail="every candidate cluster has >= 1 opportunistic insider",
        offenders=offenders,
    )


def _check_all_required_gates_true(
    candidate_gates: dict[str, dict[str, dict[str, object]]],
) -> SanityResult:
    offenders = []
    for ticker, gates in candidate_gates.items():
        ok = True
        for gate in REQUIRED_GATES:
            row = gates.get(gate)
            if row is None or row["passed"] is not True:
                ok = False
                break
        if not ok:
            offenders.append(ticker)
    return SanityResult(
        name="all_required_gates_true",
        passed=not offenders,
        detail="every required gate passed=True (never None/UNVERIFIABLE) for survivors",
        offenders=offenders,
    )


def _check_has_form4_buyer(
    candidate_gates: dict[str, dict[str, dict[str, object]]],
    classifications: dict[str, list[dict[str, object]]],
) -> SanityResult:
    offenders = [t for t in candidate_gates if not classifications.get(t)]
    return SanityResult(
        name="has_form4_buyer",
        passed=not offenders,
        detail="every candidate has >= 1 recorded Form 4 insider",
        offenders=offenders,
    )


def check_run(db_path: str, run_id: str | None = None) -> SanityReport:
    """
    Run all dossier invariants against a run (default: most recent).

    A missing DB / unknown run / zero candidates yields an all-passing report.
    """
    if run_id is None:
        run_id = repo.get_latest_run_id(db_path)
    if run_id is None:
        return SanityReport(run_id=None, candidate_count=0, results=_empty_results())

    candidate_gates, classifications = _load(db_path, run_id)
    results = [
        _check_no_microcap(candidate_gates),
        _check_positive_earnings(candidate_gates),
        _check_opportunistic_clusters(candidate_gates, classifications),
        _check_all_required_gates_true(candidate_gates),
        _check_has_form4_buyer(candidate_gates, classifications),
    ]
    return SanityReport(
        run_id=run_id,
        candidate_count=len(candidate_gates),
        results=results,
    )


def _empty_results() -> list[SanityResult]:
    return [
        SanityResult(name="no_microcap", passed=True, detail="no candidates"),
        SanityResult(name="positive_earnings", passed=True, detail="no candidates"),
        SanityResult(name="opportunistic_clusters", passed=True, detail="no candidates"),
        SanityResult(name="all_required_gates_true", passed=True, detail="no candidates"),
        SanityResult(name="has_form4_buyer", passed=True, detail="no candidates"),
    ]
