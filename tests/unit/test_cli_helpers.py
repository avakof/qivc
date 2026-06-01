"""Unit tests for CLI helper functions (build_agents, graph_cmd, _initial_state)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from typer.testing import CliRunner

from qivc.cli import main as cli_main

runner = CliRunner()


def test_build_agents_constructs_all_eight() -> None:
    """build_agents must return a namespace with all 8 data agents."""
    settings = SimpleNamespace(
        edgar_user_agent="Test User test@example.com",
        edgar_rate_limit_rps=8,
    )
    with mock.patch("edgar.set_identity"):
        agents = cli_main.build_agents(settings)

    for attr in [
        "regime",
        "form4",
        "insider_history",
        "fundamentals",
        "valuation",
        "short_interest",
        "revisions",
        "liquidity",
    ]:
        assert hasattr(agents, attr), f"missing agent: {attr}"


def test_initial_state_shape() -> None:
    state = cli_main._initial_state(
        run_id="r1",
        tickers=["UNH"],
        lookback_days=14,
        force=False,
        single_ticker_mode=True,
    )
    assert state["run_id"] == "r1"
    assert state["tickers"] == ["UNH"]
    assert state["single_ticker_mode"] is True
    # All accumulator fields initialised empty
    for key in ["transactions_by_ticker", "fundamentals", "candidates", "rejected"]:
        assert state[key] in ({}, [])


def test_graph_cmd_writes_mermaid(tmp_path: Path) -> None:
    """`qivc graph --output X.md` writes a Mermaid diagram."""
    out = tmp_path / "graph.md"
    result = runner.invoke(cli_main.app, ["graph", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    content = out.read_text()
    assert "regime_check" in content
    assert "apply_synthesis" in content
