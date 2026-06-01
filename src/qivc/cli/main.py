"""QIVC v2.0 CLI — typer-based entry point."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import typer

from qivc import __version__

app = typer.Typer(
    name="qivc",
    help="QIVC v2.0 — long-only equity research screening system.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    pass


# ---------------------------------------------------------------------------
# Agent factory (patched in tests to inject fixture-backed agents)
# ---------------------------------------------------------------------------


def build_agents(settings: Any) -> Any:
    """Construct the live data agents. Tests monkeypatch this to inject fakes."""
    from qivc.data.edgar_client import EdgarClient
    from qivc.data.estimates_agent import EstimatesAgent
    from qivc.data.form4_agent import Form4Agent
    from qivc.data.fundamentals_agent import FundamentalsAgent
    from qivc.data.insider_history_agent import InsiderHistoryAgent
    from qivc.data.liquidity_agent import LiquidityAgent
    from qivc.data.regime_agent import RegimeAgent
    from qivc.data.short_interest_agent import ShortInterestAgent
    from qivc.data.valuation_agent import ValuationAgent

    client = EdgarClient(
        user_agent=getattr(settings, "edgar_user_agent", "QIVC user@qivc.internal"),
        rate_limit_rps=getattr(settings, "edgar_rate_limit_rps", 8),
    )
    agents = SimpleNamespace()
    agents.regime = RegimeAgent(client=client)
    agents.form4 = Form4Agent(client=client)
    agents.insider_history = InsiderHistoryAgent(client=client)
    agents.fundamentals = FundamentalsAgent(client=client)
    agents.valuation = ValuationAgent(client=client)
    agents.short_interest = ShortInterestAgent(client=client)
    agents.revisions = EstimatesAgent(client=client)
    agents.liquidity = LiquidityAgent(client=client)
    return agents


def _initial_state(
    run_id: str,
    tickers: list[str],
    lookback_days: int,
    force: bool,
    single_ticker_mode: bool,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "run_timestamp": datetime.now(UTC),
        "tickers": tickers,
        "lookback_days": lookback_days,
        "force": force,
        "single_ticker_mode": single_ticker_mode,
        "regime": None,
        "transactions_by_ticker": {},
        "insider_histories": {},
        "fundamentals": {},
        "valuations": {},
        "short_interest": {},
        "revisions": {},
        "liquidity": {},
        "clusters": [],
        "filter_results": {},
        "candidates": [],
        "rejected": [],
    }


# ---------------------------------------------------------------------------
# screen
# ---------------------------------------------------------------------------


@app.command()
def screen(
    ticker: str | None = typer.Option(None, "--ticker", "-t", help="Single-ticker mode."),
    force: bool = typer.Option(False, "--force", help="Bypass risk-off entry block."),
    lookback_days: int = typer.Option(14, "--lookback-days", help="Form 4 lookback window."),
    output_dir: str | None = typer.Option(None, "--output-dir", help="Run output directory."),
) -> None:
    """Run the screening pipeline and write a Markdown + JSON dossier."""
    from qivc.config import Settings

    settings = Settings()
    out_dir: str = output_dir or str(getattr(settings, "output_dir", "data/runs"))
    run_id = asyncio.run(
        _run_screen(
            settings,
            ticker=ticker,
            force=force,
            lookback_days=lookback_days,
            output_dir=out_dir,
        )
    )
    typer.echo(f"Run complete: {run_id}")
    typer.echo(f"Output: {Path(out_dir) / run_id}")


async def _run_screen(
    settings: Any,
    ticker: str | None,
    force: bool,
    lookback_days: int,
    output_dir: str,
) -> str:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from qivc.logging_config import configure_logging
    from qivc.orchestration.graph import build_graph
    from qivc.schemas import MarketRegime, RunReport
    from qivc.storage import repositories as repo
    from qivc.synthesis import dossier

    configure_logging(
        level=getattr(settings, "log_level", "INFO"),
        fmt=getattr(settings, "log_format", "json"),
    )

    agents = build_agents(settings)
    run_id = str(uuid.uuid4())
    db_path = getattr(settings, "db_path", "data/duckdb/qivc.db")
    checkpoint_path = str(db_path).replace(".db", "_checkpoints.sqlite")

    tickers = [ticker] if ticker else []
    single_mode = ticker is not None
    state = _initial_state(run_id, tickers, lookback_days, force, single_mode)

    start = time.monotonic()
    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
        graph = build_graph(settings, agents, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": run_id}}
        final = await graph.ainvoke(state, config=config)
    duration = time.monotonic() - start

    candidates = final.get("candidates", [])
    rejected = final.get("rejected", [])
    regime = final.get("regime") or MarketRegime(
        vix_60d_sma=0.0,
        credit_spread_bps=0.0,
        yield_curve_bps=0.0,
        value_growth_12m=0.0,
        regime="risk-on",
    )
    universe_size = len(final.get("transactions_by_ticker", {})) or len(tickers)

    report = RunReport(
        run_id=run_id,
        generated_at=datetime.now(UTC),
        regime=regime,
        candidates=candidates,
        rejected=rejected,
        universe_size=universe_size,
        run_duration_seconds=duration,
    )

    run_dir = Path(output_dir) / run_id
    dossier.build_markdown(report, run_dir / "report.md")
    dossier.build_json(report, run_dir / "report.json")

    # Persist per-ticker gate results for `qivc audit`
    repo.save_filter_results(db_path, run_id, candidates, rejected)

    # Run discipline: sanity-check the just-completed run and fold the results
    # into the Markdown report. Anomalies are surfaced, not blocked.
    _append_sanity_to_report(db_path, run_id, run_dir / "report.md")

    return run_id


def _append_sanity_to_report(db_path: str, run_id: str, report_path: Path) -> None:
    """Append a sanity-check appendix to report.md; prepend a warning header on failure."""
    from qivc.sanity import check_run

    report = check_run(db_path, run_id)

    lines = ["", "---", "", "## Appendix: Sanity Checks", ""]
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        row = f"- **[{status}]** `{r.name}` — {r.detail}"
        if not r.passed:
            row += f" — offenders: {', '.join(r.offenders)}"
        lines.append(row)

    try:
        existing = report_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    if not report.all_passed:
        existing = "> ⚠️ SANITY CHECK FAILED — see appendix for details\n\n" + existing
    report_path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------

_DEFAULT_BACKTEST_UNIVERSE = "UNH,FCN,AZO,JPM,XOM,AAPL,MSFT,JNJ"


@app.command()
def backtest(
    start: str = typer.Option(..., "--start", help="Start date YYYY-MM-DD."),
    end: str = typer.Option(..., "--end", help="End date YYYY-MM-DD."),
    rebalance_freq: str = typer.Option("M", "--freq", help="Rebalance frequency: W or M."),
    universe: str = typer.Option(
        _DEFAULT_BACKTEST_UNIVERSE, "--universe", help="Comma-separated tickers."
    ),
    initial_capital: float = typer.Option(1_000_000.0, "--capital"),
    slippage_bps: float = typer.Option(5.0, "--slippage-bps"),
    commission_bps: float = typer.Option(1.0, "--commission-bps"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
) -> None:
    """Replay the screen historically (point-in-time) and report simulated returns."""
    from datetime import date as _date

    from qivc.config import Settings

    settings = Settings()
    out_dir = output_dir or "data/backtests"
    tickers = [t.strip().upper() for t in universe.split(",") if t.strip()]
    bt_id = asyncio.run(
        _run_backtest(
            settings,
            start=_date.fromisoformat(start),
            end=_date.fromisoformat(end),
            rebalance_freq=rebalance_freq,
            universe=tickers,
            initial_capital=initial_capital,
            slippage_bps=slippage_bps,
            commission_bps=commission_bps,
            output_dir=out_dir,
        )
    )
    typer.echo(f"Backtest complete: {bt_id}")
    typer.echo(f"Output: {Path(out_dir) / bt_id}")


async def _pit_quality_holdings(
    client: Any,
    universe: list[str],
    as_of: Any,
) -> list[str]:
    """
    PIT screen subset usable in a backtest: hold names whose most-recent 10-K/10-Q
    FILED before `as_of` yields a Piotroski F-Score >= 7.

    (Full insider-cluster backtesting needs historical Form 4 bulk data — the
    documented Phase 7 limitation; the live backtest screen is the quality core.)
    """
    from qivc.backtest.pit import get_fundamentals_as_of
    from qivc.data.fundamentals_agent import _compute_fundamentals
    from qivc.filters.fscore import compute_fscore

    held: list[str] = []
    for ticker in universe:
        try:
            cur = await get_fundamentals_as_of(client, ticker, as_of)
            if cur is None:
                continue
            fundamentals = _compute_fundamentals(ticker, "PIT", cur, None)
            score, _ = compute_fscore(fundamentals)
            if score >= 7:
                held.append(ticker)
        except Exception as exc:
            import structlog

            structlog.get_logger(__name__).debug("pit_screen_skip", ticker=ticker, error=str(exc))
    return held


async def _run_backtest(
    settings: Any,
    start: Any,
    end: Any,
    rebalance_freq: str,
    universe: list[str],
    initial_capital: float,
    slippage_bps: float,
    commission_bps: float,
    output_dir: str,
) -> str:
    import uuid as _uuid

    import pandas as pd
    import yfinance as yf

    from qivc.backtest.harness import _rebalance_dates, run_backtest
    from qivc.logging_config import configure_logging

    configure_logging(
        level=getattr(settings, "log_level", "INFO"),
        fmt=getattr(settings, "log_format", "json"),
    )

    # Fetch close prices for the universe + IWN benchmark.
    symbols = [*universe, "IWN"]
    raw = yf.download(symbols, start=str(start), end=str(end), progress=False, auto_adjust=True)
    close = raw.get("Close", raw)
    close = close.dropna(how="all")
    price = close[[c for c in universe if c in close.columns]]
    benchmark = close["IWN"] if "IWN" in close.columns else None

    # Pre-compute PIT holdings for each rebalance date (async), then look them up.
    agents = build_agents(settings)
    client = agents.regime._client if hasattr(agents.regime, "_client") else None
    if client is None:
        from qivc.data.edgar_client import EdgarClient

        client = EdgarClient(user_agent=settings.edgar_user_agent)

    rebal_dates = _rebalance_dates(pd.DatetimeIndex(price.index), rebalance_freq)  # type: ignore[arg-type]
    holdings: dict[Any, list[str]] = {}
    for d in rebal_dates:
        holdings[pd.Timestamp(d).date()] = await _pit_quality_holdings(
            client, universe, pd.Timestamp(d).date()
        )

    def screen_fn(as_of: Any) -> list[str]:
        return holdings.get(as_of, [])

    bt_id = str(_uuid.uuid4())
    result = run_backtest(
        start=start,
        end=end,
        rebalance_freq=rebalance_freq,  # type: ignore[arg-type]
        initial_capital=initial_capital,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        screen_fn=screen_fn,
        price_data=price,
        benchmark=benchmark,
        backtest_id=bt_id,
    )

    from qivc.backtest.io import write_backtest_outputs

    write_backtest_outputs(result, Path(output_dir) / bt_id)
    return bt_id


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


@app.command(name="graph")
def graph_cmd(
    output: str = typer.Option("/tmp/qivc_graph.png", "--output", "-o", help="Output file path."),
) -> None:
    """Render the pipeline graph to a PNG or Mermaid file."""
    from qivc.orchestration.graph import build_graph

    stub_settings = SimpleNamespace(db_path=":memory:", cmp_history_years=3, cluster_window_days=7)
    graph = build_graph(stub_settings, SimpleNamespace(), checkpointer=None)
    viz = graph.get_graph()

    out_path = Path(output)
    if output.endswith(".png"):
        try:
            out_path.write_bytes(viz.draw_mermaid_png())
            typer.echo(f"Graph PNG written to {output}")
        except Exception:
            mermaid_path = out_path.with_suffix(".md")
            mermaid_path.write_text(viz.draw_mermaid())
            typer.echo(f"PNG failed; Mermaid diagram written to {mermaid_path}")
    else:
        out_path.write_text(viz.draw_mermaid())
        typer.echo(f"Mermaid diagram written to {output}")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@app.command()
def audit(run_id: str = typer.Argument(..., help="Run ID to audit.")) -> None:
    """Show the per-ticker gate breakdown and node timings for a past run."""
    from qivc.config import Settings
    from qivc.storage import repositories as repo

    settings = Settings()
    db_path = getattr(settings, "db_path", "data/duckdb/qivc.db")

    audit_rows = repo.get_run_audit(db_path, run_id)
    filter_rows = repo.get_filter_results(db_path, run_id)
    classification_rows = repo.get_insider_classifications(db_path, run_id)

    if not audit_rows and not filter_rows and not classification_rows:
        typer.echo(f"No data found for run_id {run_id}")
        raise typer.Exit(code=1)

    typer.echo(f"=== Run audit: {run_id} ===\n")
    typer.echo("Node timings:")
    for row in audit_rows:
        typer.echo(
            f"  {row['node_name']:<28} {row['duration_ms']:>8.1f} ms  {row['result_summary']}"
        )

    # Per-insider CMP classifications grouped by ticker.
    classifications_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in classification_rows:
        classifications_by_ticker.setdefault(str(row["ticker"]), []).append(row)

    typer.echo("\nGate breakdown by ticker:")
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in filter_rows:
        by_ticker.setdefault(str(row["ticker"]), []).append(row)

    for tkr, rows in by_ticker.items():
        status = rows[0]["status"]
        failed = rows[0]["failed_gate"]
        header = f"  {tkr} — {status}"
        if failed:
            header += f" (failed: {failed})"
        typer.echo(header)
        for r in rows:
            verdict = "UNVERIFIABLE" if r["passed"] is None else "PASS" if r["passed"] else "FAIL"
            typer.echo(f"      {r['filter_name']:<20} {verdict:<13} {r['reason']}")
        # Insider classifications for this ticker (drives the insider_conviction gate).
        for c in classifications_by_ticker.get(tkr, []):
            officer = "officer" if c["is_officer"] else "non-officer"
            typer.echo(
                f"      · insider {c['name']} (CIK {c['cik']}): "
                f"{c['classification']}, {officer}, "
                f"{c['n_purchases']} buy(s), ${float(c['total_value_usd']):,.0f}, "
                f"{c['years_history']}y history"
            )


# ---------------------------------------------------------------------------
# sanity
# ---------------------------------------------------------------------------


@app.command()
def sanity(
    run_id: str | None = typer.Option(None, "--run-id", help="Run to check (default: latest)."),
) -> None:
    """Run dossier-invariant sanity checks against a run and report PASS/FAIL."""
    from qivc.config import Settings
    from qivc.sanity import check_run

    settings = Settings()
    db_path = getattr(settings, "db_path", "data/duckdb/qivc.db")
    report = check_run(db_path, run_id)

    if report.run_id is None:
        typer.echo("No run found in the database.")
        raise typer.Exit(code=0)

    typer.echo(f"=== Sanity checks: run {report.run_id} ({report.candidate_count} candidates) ===")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        line = f"  [{status}] {r.name:<24} {r.detail}"
        if not r.passed:
            line += f"  offenders: {', '.join(r.offenders)}"
        typer.echo(line)

    if not report.all_passed:
        typer.echo("\n⚠️  One or more invariants FAILED.")
        raise typer.Exit(code=1)
    typer.echo("\nAll invariants passed.")


# ---------------------------------------------------------------------------
# regime
# ---------------------------------------------------------------------------


@app.command()
def regime() -> None:
    """Print current market regime classification with all indicator values."""
    asyncio.run(_run_regime())


async def _run_regime() -> None:
    from qivc.config import Settings

    settings = Settings()
    agents = build_agents(settings)
    r = await agents.regime.fetch()
    typer.echo(f"Regime: {r.regime}")
    typer.echo(f"  VIX 60d SMA:         {r.vix_60d_sma:.2f}")
    typer.echo(f"  Credit spread (bps): {r.credit_spread_bps:.1f}")
    typer.echo(f"  Yield curve (bps):   {r.yield_curve_bps:.1f}")
    typer.echo(f"  Value-growth 12m:    {r.value_growth_12m:+.2%}")
