"""
Paper-trade config registry (Task 17) — the 90-day baseline and the 14-day fork.

Two parallel forward experiments, written to SEPARATE ledger files so neither
contaminates the other:
  - v3.0      — the BACKTESTED baseline (90-day cluster window). Untouched.
  - v3.0-w14  — an UNTESTED parameter change (14-day window) evaluated forward.
                No backtest justifies it; it is an experiment, not an improvement.

Both share the same structural GridConfig (N10_thr90_hold30) and weights
(40/30/20/10/0); ONLY the insider-aggregation window differs.
"""

from __future__ import annotations

from dataclasses import dataclass

_LEDGER_90 = "data/paper/ledger.jsonl"
_LEDGER_14 = "data/paper/ledger_w14.jsonl"


@dataclass(frozen=True)
class PaperConfig:
    key: str           # dashboard selector key
    ledger: str        # JSONL ledger file
    config: str        # tag stored in records + used to filter (parses hold target)
    grid_config: str   # parseable GridConfig for assemble_paper_portfolio
    window: int        # insider-aggregation lookback days
    label: str         # human label for the selector
    experimental: bool


PAPER_CONFIGS: dict[str, PaperConfig] = {
    "v3.0": PaperConfig(
        key="v3.0", ledger=_LEDGER_90, config="N10_thr90_hold30",
        grid_config="N10_thr90_hold30", window=90,
        label="v3.0 · 90-day (backtested baseline)", experimental=False,
    ),
    "v3.0-w14": PaperConfig(
        key="v3.0-w14", ledger=_LEDGER_14, config="N10_thr90_hold30_w14",
        grid_config="N10_thr90_hold30", window=14,
        label="v3.0-w14 · 14-day (untested fork — forward eval)", experimental=True,
    ),
}
DEFAULT_CONFIG = "v3.0"
