"""Unit tests for the Piotroski F-Score filter."""

from __future__ import annotations

from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from qivc.filters.fscore import apply, compute_fscore
from qivc.schemas import (
    CandidateInput,
    EpsRevisions,
    Fundamentals,
    Liquidity,
    ShortInterest,
    Valuation,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_fundamentals(**overrides: object) -> Fundamentals:
    """All 9 components pass by default; pass overrides to flip individual flags."""
    defaults: dict[str, object] = {
        "ticker": "TEST",
        "fiscal_period": "FY2025",
        "roa": 0.08,  # > 0  → F1
        "ocf": 5_000_000.0,  # > 0  → F2
        "delta_roa": 0.01,  # > 0  → F3
        "ocf_gt_ni": True,  #       → F4
        "delta_leverage": -0.02,  # < 0 → F5
        "delta_liquidity": 0.1,  # > 0 → F6
        "no_share_issuance": True,  #    → F7
        "delta_gross_margin": 0.02,  # > 0 → F8
        "delta_asset_turnover": 0.05,  # > 0 → F9
        "gross_profit": 400_000_000.0,
        "total_assets": 3_000_000_000.0,
    }
    defaults.update(overrides)
    return Fundamentals(**defaults)  # type: ignore[arg-type]


def _make_candidate(fundamentals: Fundamentals) -> CandidateInput:
    return CandidateInput(
        ticker=fundamentals.ticker,
        fundamentals=fundamentals,
        valuation=Valuation(
            ticker=fundamentals.ticker,
            sector="Health Care",
            gics_industry="Managed Health Care",
            forward_pe=14.0,
            ev_ebitda=10.0,
            p_tbv=3.0,
            p_affo=None,
            sector_median_metric_value=16.0,
        ),
        short_interest=ShortInterest(
            ticker=fundamentals.ticker,
            si_pct_float=0.03,
            prior_si_pct_float=0.03,
            report_date=date(2026, 5, 1),
            direction="flat",
        ),
        eps_revisions=EpsRevisions(
            ticker=fundamentals.ticker,
            delta_30d=0.1,
            delta_60d=0.05,
            delta_90d=0.02,
            accelerating=True,
        ),
        liquidity=Liquidity(
            ticker=fundamentals.ticker,
            market_cap_usd=5_000_000_000.0,
            adv_20d_usd=100_000_000.0,
            next_earnings_date=None,
            has_pending_ma=False,
        ),
        transactions=[],
    )


# ---------------------------------------------------------------------------
# compute_fscore unit tests
# ---------------------------------------------------------------------------


def test_all_nine_pass() -> None:
    f = _make_fundamentals()
    score, components = compute_fscore(f)
    assert score == 9
    assert all(v == 1 for v in components.values())


def test_all_nine_fail() -> None:
    f = _make_fundamentals(
        roa=-0.01,
        ocf=-1.0,
        delta_roa=-0.01,
        ocf_gt_ni=False,
        delta_leverage=0.01,  # increased → bad
        delta_liquidity=-0.1,  # decreased → bad
        no_share_issuance=False,
        delta_gross_margin=-0.01,
        delta_asset_turnover=-0.01,
    )
    score, components = compute_fscore(f)
    assert score == 0
    assert all(v == 0 for v in components.values())


def test_exactly_seven_pass_boundary() -> None:
    """Hand-built case: exactly 7/9 pass (delta_gross_margin and delta_asset_turnover fail)."""
    f = _make_fundamentals(
        delta_gross_margin=-0.01,  # < 0 → F8 = 0
        delta_asset_turnover=-0.05,  # < 0 → F9 = 0
    )
    score, components = compute_fscore(f)
    assert score == 7
    assert components["gross_margin_improved"] == 0
    assert components["asset_turnover_improved"] == 0


def test_components_are_zero_or_one() -> None:
    f = _make_fundamentals()
    _, components = compute_fscore(f)
    for k, v in components.items():
        assert v in (0, 1), f"Component {k}={v} is not 0 or 1"


# ---------------------------------------------------------------------------
# apply() — threshold boundary
# ---------------------------------------------------------------------------


def test_apply_pass_at_threshold_7() -> None:
    f = _make_fundamentals(delta_gross_margin=-0.01, delta_asset_turnover=-0.05)
    result = apply(_make_candidate(f), threshold=7)
    assert result.passed is True
    assert result.metric_value == 7.0
    assert result.filter_name == "f_score"


def test_apply_fail_below_threshold() -> None:
    f = _make_fundamentals(
        delta_gross_margin=-0.01,
        delta_asset_turnover=-0.05,
        delta_leverage=0.01,  # 6/9
    )
    result = apply(_make_candidate(f), threshold=7)
    assert result.passed is False
    assert result.metric_value == 6.0


def test_apply_pass_above_threshold() -> None:
    f = _make_fundamentals()  # 9/9
    result = apply(_make_candidate(f), threshold=7)
    assert result.passed is True
    assert result.metric_value == 9.0


def test_apply_boundary_score_equals_threshold() -> None:
    """Score == threshold must be a PASS (≥)."""
    f = _make_fundamentals(delta_gross_margin=-0.01, delta_asset_turnover=-0.05)
    assert compute_fscore(f)[0] == 7
    result = apply(_make_candidate(f), threshold=7)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Hypothesis property test
# ---------------------------------------------------------------------------


@given(
    roa=st.floats(allow_nan=False, allow_infinity=False),
    ocf=st.floats(allow_nan=False, allow_infinity=False),
    delta_roa=st.floats(allow_nan=False, allow_infinity=False),
    ocf_gt_ni=st.booleans(),
    delta_leverage=st.floats(allow_nan=False, allow_infinity=False),
    delta_liquidity=st.floats(allow_nan=False, allow_infinity=False),
    no_share_issuance=st.booleans(),
    delta_gross_margin=st.floats(allow_nan=False, allow_infinity=False),
    delta_asset_turnover=st.floats(allow_nan=False, allow_infinity=False),
)
@settings(max_examples=500)
def test_fscore_always_in_range(
    roa: float,
    ocf: float,
    delta_roa: float,
    ocf_gt_ni: bool,
    delta_leverage: float,
    delta_liquidity: float,
    no_share_issuance: bool,
    delta_gross_margin: float,
    delta_asset_turnover: float,
) -> None:
    """For any valid inputs, F-Score must be in [0, 9]."""
    f = _make_fundamentals(
        roa=roa,
        ocf=ocf,
        delta_roa=delta_roa,
        ocf_gt_ni=ocf_gt_ni,
        delta_leverage=delta_leverage,
        delta_liquidity=delta_liquidity,
        no_share_issuance=no_share_issuance,
        delta_gross_margin=delta_gross_margin,
        delta_asset_turnover=delta_asset_turnover,
    )
    score, _ = compute_fscore(f)
    assert 0 <= score <= 9
