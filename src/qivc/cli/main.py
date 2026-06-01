"""QIVC v2.0 CLI — typer-based entry point."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

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
# screen
# ---------------------------------------------------------------------------


@app.command()
def screen(
    ticker: str | None = typer.Option(None, "--ticker", "-t", help="Single-ticker mode."),
    force: bool = typer.Option(False, "--force", help="Bypass risk-off entry block."),
    lookback_days: int = typer.Option(14, "--lookback-days", help="Form 4 lookback window."),
) -> None:
    """Run the full screening pipeline and produce a dossier."""
    from qivc.config import Settings

    settings = Settings()
    asyncio.run(_run_screen(settings, ticker=ticker, force=force, lookback_days=lookback_days))


async def _run_screen(
    settings: object,
    ticker: str | None,
    force: bool,
    lookback_days: int,
) -> None:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from qivc.data.edgar_client import EdgarClient
    from qivc.data.estimates_agent import EstimatesAgent
    from qivc.data.form4_agent import Form4Agent
    from qivc.data.fundamentals_agent import FundamentalsAgent
    from qivc.data.insider_history_agent import InsiderHistoryAgent
    from qivc.data.liquidity_agent import LiquidityAgent
    from qivc.data.regime_agent import RegimeAgent
    from qivc.data.short_interest_agent import ShortInterestAgent
    from qivc.data.valuation_agent import ValuationAgent
    from qivc.logging_config import configure_logging
    from qivc.orchestration.graph import build_graph

    settings_obj = settings
    configure_logging(
        level=getattr(settings_obj, "log_level", "INFO"),
        fmt=getattr(settings_obj, "log_format", "json"),
    )

    client = EdgarClient(
        user_agent=getattr(settings_obj, "edgar_user_agent", "QIVC user@qivc.internal"),
        rate_limit_rps=getattr(settings_obj, "edgar_rate_limit_rps", 8),
    )

    class _Agents:
        pass

    agents = _Agents()
    agents.regime = RegimeAgent(client=client)  # type: ignore[attr-defined]
    agents.form4 = Form4Agent(client=client)  # type: ignore[attr-defined]
    agents.insider_history = InsiderHistoryAgent(client=client)  # type: ignore[attr-defined]
    agents.fundamentals = FundamentalsAgent(client=client)  # type: ignore[attr-defined]
    agents.valuation = ValuationAgent(client=client)  # type: ignore[attr-defined]
    agents.short_interest = ShortInterestAgent(client=client)  # type: ignore[attr-defined]
    agents.revisions = EstimatesAgent(client=client)  # type: ignore[attr-defined]
    agents.liquidity = LiquidityAgent(client=client)  # type: ignore[attr-defined]

    run_id = str(uuid.uuid4())
    db_path = getattr(settings_obj, "db_path", "data/duckdb/qivc.db")
    checkpoint_path = db_path.replace(".db", "_checkpoints.sqlite")

    initial_state = {
        "run_id": run_id,
        "run_timestamp": datetime.utcnow(),
        "tickers": [ticker] if ticker else [],
        "lookback_days": lookback_days,
        "force": force,
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

    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
        graph = build_graph(settings_obj, agents, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": run_id}}
        final = await graph.ainvoke(initial_state, config=config)

    candidates = final.get("candidates", [])
    rejected = final.get("rejected", [])
    typer.echo(f"Run ID: {run_id}")
    typer.echo(f"Candidates: {len(candidates)}")
    typer.echo(f"Rejected:   {len(rejected)}")

    if candidates:
        typer.echo("\nCandidates:")
        for c in candidates:
            typer.echo(f"  {c['ticker'] if isinstance(c, dict) else c.ticker}")


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


@app.command(name="graph")
def graph_cmd(
    output: str = typer.Option("/tmp/qivc_graph.png", "--output", "-o", help="Output file path."),
) -> None:
    """Render the pipeline graph to a PNG or Mermaid file."""
    from qivc.orchestration.graph import build_graph

    # Build graph with stub agents/settings for visualization only
    class _Stub:
        pass

    stub_settings = _Stub()
    stub_settings.db_path = ":memory:"  # type: ignore[attr-defined]
    stub_settings.cmp_history_years = 3  # type: ignore[attr-defined]
    stub_settings.cluster_window_days = 7  # type: ignore[attr-defined]

    class _StubAgents:
        pass

    stub_agents = _StubAgents()

    graph = build_graph(stub_settings, stub_agents, checkpointer=None)
    viz = graph.get_graph()

    out_path = Path(output)
    if output.endswith(".png"):
        try:
            png_bytes: bytes = viz.draw_mermaid_png()
            out_path.write_bytes(png_bytes)
            typer.echo(f"Graph PNG written to {output}")
        except Exception:
            # Fall back to mermaid text
            mermaid_path = out_path.with_suffix(".md")
            mermaid_path.write_text(viz.draw_mermaid())
            typer.echo(f"PNG failed; Mermaid diagram written to {mermaid_path}")
    else:
        out_path.write_text(viz.draw_mermaid())
        typer.echo(f"Mermaid diagram written to {output}")


# ---------------------------------------------------------------------------
# regime
# ---------------------------------------------------------------------------


@app.command()
def regime() -> None:
    """Print current market regime classification."""
    asyncio.run(_run_regime())


async def _run_regime() -> None:
    from qivc.config import Settings
    from qivc.data.edgar_client import EdgarClient
    from qivc.data.regime_agent import RegimeAgent

    settings = Settings()
    client = EdgarClient(user_agent=settings.edgar_user_agent)
    agent = RegimeAgent(client=client)
    r = await agent.fetch()
    typer.echo(f"Regime: {r.regime}")
    typer.echo(f"  VIX 60d SMA:         {r.vix_60d_sma:.2f}")
    typer.echo(f"  Credit spread (bps): {r.credit_spread_bps:.1f}")
    typer.echo(f"  Yield curve (bps):   {r.yield_curve_bps:.1f}")
