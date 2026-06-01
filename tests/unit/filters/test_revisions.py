"""Unit tests for the EPS revisions filter."""

from __future__ import annotations

import pytest

from qivc.filters.revisions import apply
from qivc.schemas import EpsRevisions
from tests.unit.filters.test_fscore import _make_candidate, _make_fundamentals


def _candidate_with_delta(delta_90d: float | None) -> object:
    f = _make_fundamentals()
    c = _make_candidate(f)
    rev = EpsRevisions(
        ticker="TEST",
        delta_30d=0.1 if delta_90d is not None else None,
        delta_60d=0.05 if delta_90d is not None else None,
        delta_90d=delta_90d,
        accelerating=False,
    )
    return c.model_copy(update={"eps_revisions": rev})


def test_pass_positive_delta() -> None:
    result = apply(_candidate_with_delta(0.5))  # type: ignore[arg-type]
    assert result.passed is True
    assert result.metric_value == pytest.approx(0.5)


def test_pass_zero_delta() -> None:
    """Zero is non-negative → PASS."""
    result = apply(_candidate_with_delta(0.0))  # type: ignore[arg-type]
    assert result.passed is True


def test_fail_negative_delta() -> None:
    result = apply(_candidate_with_delta(-0.1))  # type: ignore[arg-type]
    assert result.passed is False
    assert result.metric_value == pytest.approx(-0.1)


def test_unverifiable_none_delta() -> None:
    result = apply(_candidate_with_delta(None))  # type: ignore[arg-type]
    assert result.passed is None
    assert result.metric_value is None
    assert "UNVERIFIABLE" in result.reason


def test_filter_name() -> None:
    result = apply(_candidate_with_delta(0.0))  # type: ignore[arg-type]
    assert result.filter_name == "revisions"
