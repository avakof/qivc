"""Valuation filter — sector-appropriate metric vs sector median threshold."""

from __future__ import annotations

# Sector → primary valuation metric (per brief §9.2)
SECTOR_VALUATION_METRIC: dict[str, str] = {
    "Information Technology": "forward_pe",
    "Communication Services": "forward_pe",
    "Consumer Discretionary": "forward_pe",
    "Consumer Staples": "forward_pe",
    "Health Care": "forward_pe",
    "Industrials": "ev_ebitda",
    "Materials": "ev_ebitda",
    "Energy": "ev_ebitda",
    "Utilities": "ev_ebitda",
    "Financials": "p_tbv",
    "Real Estate": "p_affo",
}

# Sector → allowed multiple of sector median (1.1 = 10% premium allowed)
SECTOR_VALUATION_PREMIUM: dict[str, float] = {
    "Information Technology": 1.10,
    "Communication Services": 1.10,
    "Consumer Discretionary": 1.10,
    "Consumer Staples": 1.10,
    "Health Care": 1.10,
    "Industrials": 1.00,
    "Materials": 1.00,
    "Energy": 1.00,
    "Utilities": 1.00,
    "Financials": 1.10,
    "Real Estate": 1.00,
}
