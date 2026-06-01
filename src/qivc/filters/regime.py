"""Regime filter — pure function."""

from __future__ import annotations

from qivc.schemas import FilterResult, MarketRegime

_FILTER_NAME = "regime"


def apply(regime: MarketRegime) -> FilterResult:
    """
    risk-on  → PASS  (no position-sizing reduction)
    risk-mid → PASS  (annotated; sizing reduced by orchestration layer)
    risk-off → FAIL  (new entries blocked per brief §13)
    """
    if regime.regime == "risk-off":
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=False,
            metric_value=regime.vix_60d_sma,
            threshold=35.0,
            reason=(
                f"Market regime risk-off: VIX 60d SMA={regime.vix_60d_sma:.1f}, "
                f"credit spread={regime.credit_spread_bps:.0f}bps — new entries BLOCKED"
            ),
        )

    if regime.regime == "risk-mid":
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=True,
            metric_value=regime.vix_60d_sma,
            threshold=25.0,
            reason=(
                f"Market regime risk-mid: VIX 60d SMA={regime.vix_60d_sma:.1f}; "
                f"indicative sizing halved"
            ),
        )

    # risk-on
    return FilterResult(
        filter_name=_FILTER_NAME,
        passed=True,
        metric_value=regime.vix_60d_sma,
        threshold=25.0,
        reason=f"Market regime risk-on: VIX 60d SMA={regime.vix_60d_sma:.1f}; no restriction",
    )
