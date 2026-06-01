"""
PipelineState — the shared state threaded through every LangGraph node.

Uses TypedDict with Annotated merge-reducers so that parallel fetch nodes
can safely update independent fields (fundamentals, valuations, etc.)
without overwriting each other.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, TypedDict

from qivc.schemas import (
    Candidate,
    Cluster,
    EpsRevisions,
    FilterResult,
    Fundamentals,
    InsiderHistory,
    InsiderTransaction,
    Liquidity,
    MarketRegime,
    RejectedCandidate,
    ShortInterest,
    Valuation,
)


def _merge_lists(a: list[Any], b: list[Any]) -> list[Any]:
    return list(a) + list(b)


def _merge_dicts(a: dict[Any, Any], b: dict[Any, Any]) -> dict[Any, Any]:
    return {**a, **b}


class PipelineState(TypedDict):
    """Full mutable pipeline state.  Annotated fields use custom reducers."""

    # --- identity ---
    run_id: str
    run_timestamp: datetime

    # --- inputs (set by CLI before graph invocation) ---
    tickers: list[str]  # initial ticker list (may be empty → discover from Form 4)
    lookback_days: int
    force: bool  # if True, bypass risk-off block

    # --- regime (global) ---
    regime: MarketRegime | None

    # --- per-ticker data (parallel-populated, use merge reducer) ---
    transactions_by_ticker: Annotated[dict[str, list[InsiderTransaction]], _merge_dicts]
    insider_histories: Annotated[dict[str, InsiderHistory], _merge_dicts]
    fundamentals: Annotated[dict[str, Fundamentals], _merge_dicts]
    valuations: Annotated[dict[str, Valuation], _merge_dicts]
    short_interest: Annotated[dict[str, ShortInterest], _merge_dicts]
    revisions: Annotated[dict[str, EpsRevisions], _merge_dicts]
    liquidity: Annotated[dict[str, Liquidity], _merge_dicts]

    # --- derived ---
    clusters: Annotated[list[Cluster], _merge_lists]
    filter_results: Annotated[dict[str, list[FilterResult]], _merge_dicts]

    # --- final outputs ---
    candidates: Annotated[list[Candidate], _merge_lists]
    rejected: Annotated[list[RejectedCandidate], _merge_lists]
