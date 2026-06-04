"""
End-to-end CLI tests using Typer's CliRunner.

The data layer is patched via `qivc.cli.main.build_agents` so no live network
calls are made. DB / output paths are redirected to a tmp dir via env vars.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from qivc.cli import main as cli_main
from qivc.schemas import (
    EpsRevisions,
    Fundamentals,
    InsiderHistory,
    Liquidity,
    MarketRegime,
    RunReport,
    ShortInterest,
    Valuation,
)
from qivc.synthesis.dossier import DISCLAIMER

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture-backed agents — UNH survives all gates (strong fundamentals)
# ---------------------------------------------------------------------------


def _strong_agents() -> Any:
    agents = SimpleNamespace()

    async def regime_fetch(**kw: Any) -> MarketRegime:
        return MarketRegime(
            vix_60d_sma=18.0,
            credit_spread_bps=150.0,
            yield_curve_bps=30.0,
            value_growth_12m=0.02,
            regime="risk-on",
        )

    async def history_fetch(**kw: Any) -> InsiderHistory:
        return InsiderHistory(cik=kw.get("cik", "0"), transactions=[], years_of_history=3)

    async def fund_fetch(**kw: Any) -> Fundamentals:
        return Fundamentals(
            ticker=kw.get("ticker", "UNH"),
            fiscal_period="FY2025",
            roa=0.10,
            ocf=5_000_000.0,
            delta_roa=0.02,
            ocf_gt_ni=True,
            delta_leverage=-0.05,
            delta_liquidity=0.2,
            no_share_issuance=True,
            delta_gross_margin=0.03,
            delta_asset_turnover=0.05,
            gross_profit=1_200_000_000.0,  # GP/A = 0.40
            total_assets=3_000_000_000.0,
        )

    async def val_fetch(**kw: Any) -> Valuation:
        return Valuation(
            ticker=kw.get("ticker", "UNH"),
            sector="Health Care",
            gics_industry="Managed Health Care",
            forward_pe=14.0,
            ev_ebitda=None,
            p_tbv=None,
            p_affo=None,
            sector_median_metric_value=18.0,  # 14 below 18x1.10 -> pass, ~22pct discount
        )

    async def si_fetch(**kw: Any) -> ShortInterest:
        return ShortInterest(
            ticker=kw.get("ticker", "UNH"),
            si_pct_float=0.02,
            prior_si_pct_float=0.03,
            report_date=date(2026, 5, 1),
            direction="falling",
        )

    async def rev_fetch(**kw: Any) -> EpsRevisions:
        return EpsRevisions(
            ticker=kw.get("ticker", "UNH"),
            delta_30d=0.1,
            delta_60d=0.05,
            delta_90d=0.02,
            accelerating=True,
        )

    async def liq_fetch(**kw: Any) -> Liquidity:
        return Liquidity(
            ticker=kw.get("ticker", "UNH"),
            market_cap_usd=400_000_000_000.0,
            adv_20d_usd=2_000_000_000.0,
            next_earnings_date=None,
            has_pending_ma=False,
        )

    async def form4_fetch(**kw: Any) -> list[Any]:
        return []

    agents.regime = SimpleNamespace(fetch=regime_fetch)
    agents.form4 = SimpleNamespace(fetch=form4_fetch)
    agents.insider_history = SimpleNamespace(fetch=history_fetch)
    agents.fundamentals = SimpleNamespace(fetch=fund_fetch)
    agents.valuation = SimpleNamespace(fetch=val_fetch)
    agents.short_interest = SimpleNamespace(fetch=si_fetch)
    agents.revisions = SimpleNamespace(fetch=rev_fetch)
    agents.liquidity = SimpleNamespace(fetch=liq_fetch)
    return agents


@pytest.fixture
def patched_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect DB + output dir to tmp, set a dummy user agent, patch agents."""
    monkeypatch.setenv("QIVC_EDGAR_USER_AGENT", "Test User test@example.com")
    monkeypatch.setenv("QIVC_DB_PATH", str(tmp_path / "qivc.db"))
    monkeypatch.setenv("QIVC_OUTPUT_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli_main, "build_agents", lambda settings: _strong_agents())
    return tmp_path


# ---------------------------------------------------------------------------
# screen --ticker UNH
# ---------------------------------------------------------------------------


def test_screen_single_ticker_produces_outputs(patched_env: Path) -> None:
    result = runner.invoke(cli_main.app, ["screen", "--ticker", "UNH"])
    assert result.exit_code == 0, result.output

    runs_dir = patched_env / "runs"
    assert runs_dir.exists(), "output directory should be created"

    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    md_path = run_dir / "report.md"
    json_path = run_dir / "report.json"
    assert md_path.exists()
    assert json_path.exists()

    # Disclaimer verbatim
    md = md_path.read_text()
    assert DISCLAIMER in md

    # Every gate appears in the markdown for UNH (it survives all gates)
    for gate in [
        "regime",
        "insider_conviction",
        "f_score",
        "gp_a",
        "valuation",
        "revisions",
        "short_interest",
        "liquidity",
    ]:
        assert gate in md, f"gate '{gate}' missing from markdown"

    assert "UNH" in md

    # JSON validates against RunReport
    data = json.loads(json_path.read_text())
    report = RunReport.model_validate(data)
    assert report.run_id == run_dir.name
    assert any(c.ticker == "UNH" for c in report.candidates)


def test_screen_json_is_pretty_printed(patched_env: Path) -> None:
    result = runner.invoke(cli_main.app, ["screen", "--ticker", "UNH"])
    assert result.exit_code == 0, result.output
    run_dir = next((patched_env / "runs").iterdir())
    raw = (run_dir / "report.json").read_text()
    assert "\n  " in raw  # indented → pretty-printed


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def test_audit_after_screen(patched_env: Path) -> None:
    screen_result = runner.invoke(cli_main.app, ["screen", "--ticker", "UNH"])
    assert screen_result.exit_code == 0, screen_result.output
    run_id = next((patched_env / "runs").iterdir()).name

    audit_result = runner.invoke(cli_main.app, ["audit", run_id])
    assert audit_result.exit_code == 0, audit_result.output
    assert run_id in audit_result.output
    assert "UNH" in audit_result.output
    # Node timings + gate breakdown headers present
    assert "Node timings" in audit_result.output
    assert "Gate breakdown" in audit_result.output
    # A specific gate shows up in the breakdown
    assert "f_score" in audit_result.output


def test_screen_writes_sanity_appendix(patched_env: Path) -> None:
    """`qivc screen` folds sanity-check results into report.md."""
    result = runner.invoke(cli_main.app, ["screen", "--ticker", "UNH"])
    assert result.exit_code == 0, result.output
    run_dir = next((patched_env / "runs").iterdir())
    md = (run_dir / "report.md").read_text()
    assert "Appendix: Sanity Checks" in md
    assert "no_microcap" in md


def test_sanity_command_passes_on_clean_run(patched_env: Path) -> None:
    """`qivc sanity` reports PASS when invariants hold for the latest run."""
    from qivc.sanity import REQUIRED_GATES
    from qivc.schemas import Candidate, Cluster, FilterResult, InsiderTransaction
    from qivc.storage import repositories as repo

    db = str(patched_env / "qivc.db")
    txn = InsiderTransaction(
        cik="C1",
        name="Insider",
        title="CEO",
        ticker="GOOD",
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
    cluster = Cluster(
        ticker="GOOD",
        transactions=[txn],
        track="B",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 1),
        total_value_usd=300_000.0,
    )
    gates = [
        FilterResult(
            filter_name=g,
            passed=True,
            metric_value=(5e8 if g == "liquidity" else 1.0),
            threshold=None,
            reason="ok",
        )
        for g in REQUIRED_GATES
    ]
    cand = Candidate(
        ticker="GOOD",
        cluster=cluster,
        filter_results=gates,
        sector="Industrials",
        gics_industry_group="Test",
    )
    from datetime import datetime

    repo.log_node_execution(
        db, "clean-run", "apply_synthesis", datetime(2026, 6, 1), datetime(2026, 6, 1), 1.0, "ok"
    )
    repo.save_filter_results(db, "clean-run", [cand], [])
    repo.save_insider_classifications(
        db,
        "clean-run",
        [
            {
                "ticker": "GOOD",
                "cik": "C1",
                "name": "Insider",
                "classification": "opportunistic",
                "years_history": 3,
                "n_purchases": 1,
                "total_value_usd": 300_000.0,
                "is_officer": True,
            }
        ],
    )

    result = runner.invoke(cli_main.app, ["sanity", "--run-id", "clean-run"])
    assert result.exit_code == 0, result.output
    assert "All invariants passed" in result.output


def test_sanity_command_fails_on_microcap(patched_env: Path) -> None:
    """`qivc sanity` exits non-zero and names the offender on a violation."""
    from qivc.sanity import REQUIRED_GATES
    from qivc.schemas import Candidate, Cluster, FilterResult, InsiderTransaction
    from qivc.storage import repositories as repo

    db = str(patched_env / "qivc.db")
    txn = InsiderTransaction(
        cik="C1",
        name="Insider",
        title="CEO",
        ticker="GPUS",
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
    cluster = Cluster(
        ticker="GPUS",
        transactions=[txn],
        track="B",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 1),
        total_value_usd=1.0,
    )
    gates = [
        FilterResult(
            filter_name=g,
            passed=True,
            metric_value=(9e7 if g == "liquidity" else 1.0),  # micro-cap!
            threshold=None,
            reason="ok",
        )
        for g in REQUIRED_GATES
    ]
    cand = Candidate(ticker="GPUS", cluster=cluster, filter_results=gates)
    from datetime import datetime

    repo.log_node_execution(
        db, "bad-run", "apply_synthesis", datetime(2026, 6, 1), datetime(2026, 6, 1), 1.0, "ok"
    )
    repo.save_filter_results(db, "bad-run", [cand], [])
    repo.save_insider_classifications(
        db,
        "bad-run",
        [
            {
                "ticker": "GPUS",
                "cik": "C1",
                "name": "Insider",
                "classification": "opportunistic",
                "years_history": 3,
                "n_purchases": 1,
                "total_value_usd": 1.0,
                "is_officer": True,
            }
        ],
    )

    result = runner.invoke(cli_main.app, ["sanity", "--run-id", "bad-run"])
    assert result.exit_code == 1
    assert "no_microcap" in result.output
    assert "GPUS" in result.output


def test_report_gets_silent_failure_warning(patched_env: Path) -> None:
    """report.md gets the ⚠️ silent-failure header for an errored, zero-candidate run."""
    from datetime import datetime

    from qivc.storage import repositories as repo

    db = str(patched_env / "qivc.db")
    repo.log_node_execution(
        db,
        "errored-run",
        "regime_check",
        datetime(2026, 6, 1),
        datetime(2026, 6, 1),
        1.0,
        "ERROR: Market regime is risk-off.",
    )
    report_path = patched_env / "report.md"
    report_path.write_text("# QIVC Screen\n\nbody\n", encoding="utf-8")

    cli_main._append_sanity_to_report(db, "errored-run", report_path)

    md = report_path.read_text()
    assert md.startswith("> ⚠️ SANITY CHECK FAILED")
    assert "status=errored" in md
    assert "review run_audit" in md
    assert "Appendix: Sanity Checks" in md


def test_sanity_command_fails_on_silent_failure(patched_env: Path) -> None:
    """`qivc sanity` exits 1 and names the run when status!=completed AND no candidates."""
    from datetime import datetime

    from qivc.storage import repositories as repo

    db = str(patched_env / "qivc.db")
    # Aborted run: an ERROR node, no apply_synthesis, zero candidates.
    repo.log_node_execution(
        db,
        "errored-run",
        "regime_check",
        datetime(2026, 6, 1),
        datetime(2026, 6, 1),
        1.0,
        "ERROR: Market regime is risk-off.",
    )

    result = runner.invoke(cli_main.app, ["sanity", "--run-id", "errored-run"])
    assert result.exit_code == 1
    assert "no_silent_failure" in result.output
    assert "errored-run" in result.output


def test_audit_shows_insider_classifications(patched_env: Path) -> None:
    """`qivc audit` surfaces per-insider CMP labels alongside the gate breakdown."""
    from qivc.schemas import FilterResult, RejectedCandidate
    from qivc.storage import repositories as repo

    db = str(patched_env / "qivc.db")
    run_id = "audit-class-test"

    rejected = RejectedCandidate(
        ticker="FCN",
        failed_gate="insider_conviction",
        reason="No Track A or Track B cluster detected",
        filter_results=[
            FilterResult(
                filter_name="insider_conviction",
                passed=False,
                metric_value=0.0,
                threshold=1.0,
                reason="No Track A or Track B cluster detected",
            ),
        ],
    )
    repo.save_filter_results(db, run_id, [], [rejected])
    repo.save_insider_classifications(
        db,
        run_id,
        [
            {
                "ticker": "FCN",
                "cik": "0001597949",
                "name": "Steven Henry Gunby",
                "classification": "opportunistic",
                "years_history": 3,
                "n_purchases": 2,
                "total_value_usd": 1_441_707.0,
                "is_officer": True,
            }
        ],
    )

    result = runner.invoke(cli_main.app, ["audit", run_id])
    assert result.exit_code == 0, result.output
    assert "Steven Henry Gunby" in result.output
    assert "opportunistic" in result.output
    assert "officer" in result.output


def test_audit_unknown_run_id_exits_nonzero(patched_env: Path) -> None:
    result = runner.invoke(cli_main.app, ["audit", "no-such-run"])
    assert result.exit_code == 1
    assert "No data found" in result.output


# ---------------------------------------------------------------------------
# regime
# ---------------------------------------------------------------------------


def test_regime_prints_all_indicators(patched_env: Path) -> None:
    result = runner.invoke(cli_main.app, ["regime"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "risk-on" in out
    assert "VIX 60d SMA" in out
    assert "Credit spread" in out
    assert "Yield curve" in out
    assert "Value-growth 12m" in out


# ---------------------------------------------------------------------------
# paper — forward out-of-sample paper trading (no orders, research only)
# ---------------------------------------------------------------------------


def test_paper_writes_ledger_and_warns(patched_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`qivc paper` builds an intended book, appends a ledger line, and warns."""
    from datetime import date

    from qivc.backtest.portfolio_constructor import Position

    regime = MarketRegime(
        vix_60d_sma=18.0,
        credit_spread_bps=150.0,
        yield_curve_bps=30.0,
        value_growth_12m=0.02,
        regime="risk-on",
    )
    fake_positions = [
        Position("AAA", "Energy", 0.5, date(2026, 6, 2), 0.71),
        Position("BBB", "Health Care", 0.5, date(2026, 6, 2), 0.69),
    ]
    seen_prev: list[list] = []

    _fc = {"insider": 0.8, "quality": 0.4, "valuation": 0.6, "momentum": 0.5, "technical": 0.5}
    fake_components = {"AAA": _fc, "BBB": _fc}

    fake_inputs = {"AAA": {"fscore": 7, "gpa": 0.3}, "BBB": {"fscore": 5, "gpa": 0.2}}

    def fake_assemble(settings, as_of, config, prev_positions, cache_dir="x"):  # type: ignore[no-untyped-def]
        seen_prev.append(prev_positions)
        return fake_positions, 7, regime, fake_components, "fred_live", fake_inputs

    monkeypatch.setattr(cli_main, "assemble_paper_portfolio", fake_assemble)
    ledger = patched_env / "paper" / "ledger.jsonl"

    result = runner.invoke(
        cli_main.app,
        ["paper", "--as-of", "2026-06-02", "--config", "N10_thr90_hold30",
         "--ledger", str(ledger)],
    )
    assert result.exit_code == 0, result.output
    assert "DO NOT DEPLOY" in result.output
    assert "NO ORDERS" in result.output
    assert "AAA" in result.output and "BBB" in result.output

    assert ledger.exists()
    rec = json.loads(ledger.read_text().splitlines()[0])
    assert rec["config"] == "N10_thr90_hold30"
    assert rec["as_of"] == "2026-06-02"
    assert rec["equity_pct"] == 100.0
    assert {p["ticker"] for p in rec["positions"]} == {"AAA", "BBB"}
    assert "RESEARCH ONLY" in rec["disclaimer"]
    # first run threads no prior positions
    assert seen_prev[0] == []

    # second run reads the prior record and threads its positions back in
    result2 = runner.invoke(
        cli_main.app,
        ["paper", "--as-of", "2026-07-01", "--config", "N10_thr90_hold30",
         "--ledger", str(ledger)],
    )
    assert result2.exit_code == 0, result2.output
    assert {p.ticker for p in seen_prev[1]} == {"AAA", "BBB"}
    assert len(ledger.read_text().splitlines()) == 2


# ---------------------------------------------------------------------------
# dashboard (one-shot) — regression: still writes a static file
# ---------------------------------------------------------------------------


def test_dashboard_oneshot_writes_file(patched_env: Path) -> None:
    out = patched_env / "dash.html"
    result = runner.invoke(
        cli_main.app,
        ["dashboard", "--no-open", "--ledger", str(patched_env / "empty.jsonl"),
         "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "not validated" in out.read_text().lower()  # permanent banner intact


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_version() -> None:
    result = runner.invoke(cli_main.app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
