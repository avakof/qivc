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
from qivc.dashboard.detail import (
    build_ticker_detail,
    company_profile,
    ticker_form4_history,
)
from qivc.dashboard.prices import YFinancePriceProvider
from qivc.dashboard.render import render_detail_html, render_html
from qivc.dashboard.server import create_server, make_handler

__all__ = [
    "BASELINE_WEIGHTS",
    "PriceProvider",
    "YFinancePriceProvider",
    "build_dashboard_data",
    "build_ticker_detail",
    "company_profile",
    "create_server",
    "make_handler",
    "read_records",
    "reconstruct_trades",
    "render_detail_html",
    "render_html",
    "sample_size_label",
    "ticker_form4_history",
    "top3_concentration",
]
