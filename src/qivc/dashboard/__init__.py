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
from qivc.dashboard.configs import DEFAULT_CONFIG, PAPER_CONFIGS, PaperConfig
from qivc.dashboard.detail import (
    build_ticker_detail,
    company_profile,
    technical_indicators,
    technical_panel,
    ticker_form4_history,
)
from qivc.dashboard.indicators import compute_indicators
from qivc.dashboard.prices import YFinancePriceProvider
from qivc.dashboard.render import render_compare_html, render_detail_html, render_html
from qivc.dashboard.server import create_server, make_handler

__all__ = [
    "BASELINE_WEIGHTS",
    "DEFAULT_CONFIG",
    "PAPER_CONFIGS",
    "PaperConfig",
    "PriceProvider",
    "YFinancePriceProvider",
    "build_dashboard_data",
    "build_ticker_detail",
    "company_profile",
    "compute_indicators",
    "create_server",
    "make_handler",
    "read_records",
    "reconstruct_trades",
    "render_compare_html",
    "render_detail_html",
    "render_html",
    "sample_size_label",
    "technical_indicators",
    "technical_panel",
    "ticker_form4_history",
    "top3_concentration",
]
