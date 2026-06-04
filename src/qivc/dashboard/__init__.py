"""Live forward paper-trade dashboard (Task 15) — reads the qivc paper ledger only."""

from __future__ import annotations

from qivc.dashboard.build import (
    BASELINE_WEIGHTS,
    PriceProvider,
    build_dashboard_data,
    read_records,
    reconstruct_trades,
    sample_size_label,
    top3_concentration,
)
from qivc.dashboard.prices import YFinancePriceProvider
from qivc.dashboard.render import render_html

__all__ = [
    "BASELINE_WEIGHTS",
    "PriceProvider",
    "YFinancePriceProvider",
    "build_dashboard_data",
    "read_records",
    "reconstruct_trades",
    "render_html",
    "sample_size_label",
    "top3_concentration",
]
