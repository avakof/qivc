"""
LangGraph StateGraph — QIVC pipeline definition.

Graph topology:

  START
    → regime_check
    → ingest_form4
    → [fan-out]
       fetch_insider_histories
       fetch_fundamentals
       fetch_valuation
       fetch_short_interest
       fetch_revisions
       fetch_liquidity
    → [join] apply_cluster_detection
    → apply_filters
    → apply_synthesis
    → END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from qivc.orchestration.nodes import make_nodes
from qivc.orchestration.state import PipelineState

_FETCH_NODES = [
    "fetch_insider_histories",
    "fetch_fundamentals",
    "fetch_valuation",
    "fetch_short_interest",
    "fetch_revisions",
    "fetch_liquidity",
]


def build_graph(
    settings: Any,
    agents: Any,
    checkpointer: Any | None = None,
) -> Any:
    """
    Compile and return a LangGraph `CompiledStateGraph`.

    Parameters
    ----------
    settings:
        Application settings (used inside nodes via closure).
    agents:
        Object with attributes: .regime, .form4, .insider_history,
        .fundamentals, .valuation, .short_interest, .revisions, .liquidity
    checkpointer:
        Optional AsyncSqliteSaver for checkpoint-based resumability.
        If None, the graph runs without checkpointing.
    """
    nodes = make_nodes(agents, settings)

    builder: StateGraph[PipelineState] = StateGraph(PipelineState)

    # Add all nodes
    for name, fn in nodes.items():
        builder.add_node(name, fn)

    # Linear: START → regime_check → ingest_form4
    builder.add_edge(START, "regime_check")
    builder.add_edge("regime_check", "ingest_form4")

    # Fan-out: ingest_form4 → each fetch node (parallel)
    for fetch_node in _FETCH_NODES:
        builder.add_edge("ingest_form4", fetch_node)

    # Join: every fetch node → apply_cluster_detection
    for fetch_node in _FETCH_NODES:
        builder.add_edge(fetch_node, "apply_cluster_detection")

    # Sequential: apply_cluster_detection → apply_filters → apply_synthesis → END
    builder.add_edge("apply_cluster_detection", "apply_filters")
    builder.add_edge("apply_filters", "apply_synthesis")
    builder.add_edge("apply_synthesis", END)

    return builder.compile(checkpointer=checkpointer)
