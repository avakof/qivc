"""
QIVC forward paper-trading support (Phase 4A close).

Records what the v3.0 composite *would* do going forward — genuinely unseen,
survivor-bias-free data — to accumulate an out-of-sample track record. This is a
RESEARCH data-collection tool: it places no orders and asserts no edge. See the
``qivc paper`` CLI command and STRATEGY_NOTES.md "Phase 4A — CLOSED / DO NOT
DEPLOY".
"""

from __future__ import annotations

from qivc.paper.forward import (
    PaperPosition,
    PaperRecord,
    append_record,
    assemble_paper_portfolio,
    build_record,
    parse_config,
    read_last_record,
)

__all__ = [
    "PaperPosition",
    "PaperRecord",
    "append_record",
    "assemble_paper_portfolio",
    "build_record",
    "parse_config",
    "read_last_record",
]
