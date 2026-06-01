"""EPS revisions filter — pure function."""

from __future__ import annotations

from qivc.schemas import CandidateInput, FilterResult

_FILTER_NAME = "revisions"


def apply(c: CandidateInput) -> FilterResult:
    """
    Pass if 90-day EPS revision delta >= 0 (non-negative).
    Returns UNVERIFIABLE (passed=None) when delta_90d is None.
    """
    delta = c.eps_revisions.delta_90d

    if delta is None:
        return FilterResult(
            filter_name=_FILTER_NAME,
            passed=None,
            metric_value=None,
            threshold=0.0,
            reason="UNVERIFIABLE: 90-day EPS revision delta not available",
        )

    passed = delta >= 0.0
    return FilterResult(
        filter_name=_FILTER_NAME,
        passed=passed,
        metric_value=delta,
        threshold=0.0,
        reason=(
            f"90d EPS revision {delta:+.4f} "
            f"({'≥' if passed else '<'} 0; "
            f"analysts {'not cutting' if passed else 'cutting'} estimates)"
        ),
    )
