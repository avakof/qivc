"""Valuation filter — sector-appropriate metric vs sector median threshold."""

from __future__ import annotations

from qivc.schemas import CandidateInput, FilterResult

_FILTER_NAME = "valuation"

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


def _get_metric_value(c: CandidateInput, metric: str) -> float | None:
    v = c.valuation
    mapping: dict[str, float | None] = {
        "forward_pe": v.forward_pe,
        "ev_ebitda": v.ev_ebitda,
        "p_tbv": v.p_tbv,
        "p_affo": v.p_affo,
    }
    return mapping.get(metric)


def apply(c: CandidateInput) -> FilterResult:
    """
    Pass if the sector-appropriate metric is at or below sector_median * premium_allowance.
    Returns UNVERIFIABLE (passed=None) when either the metric or sector median is missing.
    """
    sector = c.valuation.sector
    metric_name = SECTOR_VALUATION_METRIC.get(sector, "forward_pe")
    premium = SECTOR_VALUATION_PREMIUM.get(sector, 1.0)

    metric_value = _get_metric_value(c, metric_name)
    sector_median = c.valuation.sector_median_metric_value

    if metric_value is None:
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=None,
            metric_value=None,
            threshold=None,
            reason=f"UNVERIFIABLE: {metric_name} not available for {c.ticker}",
        )

    if sector_median is None:
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=None,
            metric_value=metric_value,
            threshold=None,
            reason=f"UNVERIFIABLE: no sector median available for {sector}",
        )

    threshold = sector_median * premium
    passed = metric_value <= threshold
    return FilterResult(
        filter_name=_FILTER_NAME,
        passed=passed,
        metric_value=metric_value,
        threshold=threshold,
        reason=(
            f"{metric_name} {metric_value:.2f} {'≤' if passed else '>'} "
            f"sector median {sector_median:.2f} x {premium:.2f} = {threshold:.2f} "
            f"(sector: {sector})"
        ),
    )
