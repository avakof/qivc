"""Unit tests for FundamentalsAgent — 9 Piotroski inputs from FCN fixture."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from qivc.data.fundamentals_agent import FundamentalsAgent, _compute_fundamentals

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"

# ---------------------------------------------------------------------------
# Load FCN fixture values (hand-computed expected outputs)
# ---------------------------------------------------------------------------
_FCN = json.loads((FIXTURES / "tenk_fcn.json").read_text())

# Cur
_CUR_NI = _FCN["net_income"]  # 270_871_000
_CUR_TA = _FCN["total_assets"]  # 3_490_528_000
_CUR_OCF = _FCN["operating_cash_flow"]  # 152_132_000
_CUR_REV = _FCN["revenue"]  # 3_788_857_000
_CUR_OP = _FCN["operating_income"]  # 389_077_000 (used as gross_profit proxy)

# Prior
_PRIOR = _FCN["prior"]
_PR_NI = _PRIOR["net_income"]  # 280_088_000
_PR_TA = _PRIOR["total_assets"]  # 3_596_830_000
_PR_OCF = _PRIOR["operating_cash_flow"]  # 395_097_000
_PR_REV = _PRIOR["revenue"]  # 3_698_652_000
_PR_OP = _PRIOR["operating_income"]  # 347_362_000

# Pre-compute expected values
_EXP_ROA = _CUR_NI / _CUR_TA
_EXP_PRIOR_ROA = _PR_NI / _PR_TA
_EXP_DELTA_ROA = _EXP_ROA - _EXP_PRIOR_ROA
_EXP_OCF_GT_NI = (_CUR_OCF / _CUR_TA) > _EXP_ROA  # False (152M/3.49B < ROA)
_EXP_DELTA_GM = (_CUR_OP / _CUR_REV) - (_PR_OP / _PR_REV)
_EXP_DELTA_AT = (_CUR_REV / _CUR_TA) - (_PR_REV / _PR_TA)


def _make_fin(data: dict[str, Any], extra: dict[str, Any] | None = None) -> Any:
    """Build a SimpleNamespace that mimics edgar.Financials."""
    d = dict(data)
    if extra:
        d.update(extra)

    def _get(key: str) -> float | None:
        return d.get(key)

    fin = SimpleNamespace(
        get_net_income=lambda: _get("net_income"),
        get_total_assets=lambda: _get("total_assets"),
        get_operating_cash_flow=lambda: _get("operating_cash_flow"),
        get_revenue=lambda: _get("revenue"),
        get_operating_income=lambda: _get("operating_income"),
        get_shares_outstanding_basic=lambda: _get("shares_outstanding_basic"),
        # Liquidity / leverage proxies — not in this fixture
        get_current_assets=lambda: _get("current_assets"),
        get_current_liabilities=lambda: _get("current_liabilities"),
        get_total_liabilities=lambda: _get("total_liabilities"),
        balance_sheet=lambda: None,
    )
    return fin


# ---------------------------------------------------------------------------
# _compute_fundamentals (pure function) tests
# ---------------------------------------------------------------------------


def test_roa_computed_correctly() -> None:
    cur = _make_fin(_FCN)
    prior = _make_fin(_PRIOR)
    result = _compute_fundamentals("FCN", "FY2024", cur, prior)
    assert result.roa == pytest.approx(_EXP_ROA, rel=1e-4)


def test_delta_roa() -> None:
    cur = _make_fin(_FCN)
    prior = _make_fin(_PRIOR)
    result = _compute_fundamentals("FCN", "FY2024", cur, prior)
    assert result.delta_roa == pytest.approx(_EXP_DELTA_ROA, rel=1e-4)
    # FCN: current ROA slightly below prior → delta_roa is negative
    assert result.delta_roa < 0


def test_ocf_gt_ni_false_for_fcn() -> None:
    """For FCN, OCF/TA (4.4%) < ROA (7.8%) → ocf_gt_ni should be False."""
    cur = _make_fin(_FCN)
    prior = _make_fin(_PRIOR)
    result = _compute_fundamentals("FCN", "FY2024", cur, prior)
    assert result.ocf_gt_ni is False


def test_delta_gross_margin_positive() -> None:
    cur = _make_fin(_FCN)
    prior = _make_fin(_PRIOR)
    result = _compute_fundamentals("FCN", "FY2024", cur, prior)
    assert result.delta_gross_margin == pytest.approx(_EXP_DELTA_GM, rel=1e-4)
    assert result.delta_gross_margin > 0


def test_delta_asset_turnover_positive() -> None:
    cur = _make_fin(_FCN)
    prior = _make_fin(_PRIOR)
    result = _compute_fundamentals("FCN", "FY2024", cur, prior)
    assert result.delta_asset_turnover == pytest.approx(_EXP_DELTA_AT, rel=1e-4)
    assert result.delta_asset_turnover > 0


def test_no_share_issuance_true_when_shares_equal() -> None:
    """If both periods have None shares, defaults to 0==0 → True (no issuance)."""
    cur = _make_fin(_FCN)
    prior = _make_fin(_PRIOR)
    result = _compute_fundamentals("FCN", "FY2024", cur, prior)
    assert result.no_share_issuance is True


def test_gross_profit_and_total_assets() -> None:
    cur = _make_fin(_FCN)
    prior = _make_fin(_PRIOR)
    result = _compute_fundamentals("FCN", "FY2024", cur, prior)
    assert result.gross_profit == pytest.approx(_CUR_OP, rel=1e-4)
    assert result.total_assets == pytest.approx(_CUR_TA, rel=1e-4)


def test_no_prior_year_defaults_gracefully() -> None:
    """Without prior data, all deltas default to comparing against 0."""
    cur = _make_fin(_FCN)
    result = _compute_fundamentals("FCN", "FY2024", cur, None)
    assert result.roa == pytest.approx(_EXP_ROA, rel=1e-4)
    # Delta vs zero-baseline
    assert result.delta_roa == pytest.approx(_EXP_ROA, rel=1e-4)


# ---------------------------------------------------------------------------
# FundamentalsAgent (integration with mock client)
# ---------------------------------------------------------------------------


async def test_fundamentals_agent_fetch() -> None:
    cur_fin = _make_fin(_FCN)
    prior_fin = _make_fin(_PRIOR)

    client = mock.AsyncMock()
    client.get_financials_10k = mock.AsyncMock(return_value=[cur_fin, prior_fin])

    agent = object.__new__(FundamentalsAgent)
    agent._client = client  # type: ignore[attr-defined]

    result = await agent.fetch(ticker="FCN")
    assert result.ticker == "FCN"
    assert result.roa == pytest.approx(_EXP_ROA, rel=1e-4)


async def test_fundamentals_agent_no_data_raises() -> None:
    from qivc.exceptions import QivcDataError

    client = mock.AsyncMock()
    client.get_financials_10k = mock.AsyncMock(return_value=[])

    agent = FundamentalsAgent(client=client)
    assert agent.source_name == "sec_edgar_xbrl_10k"

    with pytest.raises(QivcDataError):
        await agent.fetch(ticker="FCN")


async def test_fundamentals_agent_client_error_raises() -> None:
    from qivc.exceptions import QivcDataError

    client = mock.AsyncMock()
    client.get_financials_10k = mock.AsyncMock(side_effect=Exception("network"))
    agent = FundamentalsAgent(client=client)

    with pytest.raises(QivcDataError):
        await agent.fetch(ticker="FCN")


def test_safe_float_string_in_fundamentals() -> None:
    from qivc.data.fundamentals_agent import _safe_float

    assert _safe_float("not-a-number") == 0.0
    assert _safe_float(None) == 0.0
    assert _safe_float(3.14) == pytest.approx(3.14)


async def test_fundamentals_agent_compute_raises_wraps_error() -> None:
    """If _compute_fundamentals raises, QivcDataError is raised."""
    from qivc.exceptions import QivcDataError

    cur_fin = _make_fin(_FCN)
    # RuntimeError propagates past _safe_float (which only catches TypeError/ValueError)
    cur_fin.get_net_income = mock.MagicMock(side_effect=RuntimeError("unexpected"))  # type: ignore[assignment]

    client = mock.AsyncMock()
    client.get_financials_10k = mock.AsyncMock(return_value=[cur_fin])
    agent = FundamentalsAgent(client=client)

    with pytest.raises(QivcDataError):
        await agent.fetch(ticker="FCN")
