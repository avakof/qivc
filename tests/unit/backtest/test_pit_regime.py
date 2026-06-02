"""Unit tests for PIT regime — FRED sliced strictly before as_of (Task 10)."""

from __future__ import annotations

import datetime as dt

from qivc.backtest.pit_regime import regime_as_of


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    """Serves canned FRED CSVs by series id; records which rows exist."""

    def __init__(self, series: dict[str, str]) -> None:
        self._series = series

    def get(self, url: str) -> _FakeResp:
        sid = url.split("id=")[-1]
        return _FakeResp(self._series[sid])

    def close(self) -> None:
        return None


def _csv(rows: list[tuple[str, float]]) -> str:
    body = "\n".join(f"{d},{v}" for d, v in rows)
    return "DATE,VALUE\n" + body


def test_regime_excludes_observations_on_or_after_as_of() -> None:
    # VIX spikes to 50 ON as_of and after, but is calm (15) before -> must read calm.
    vix = _csv(
        [(f"2025-03-{d:02d}", 15.0) for d in range(1, 28)]
        + [("2025-04-01", 50.0), ("2025-04-02", 60.0)]
    )
    flat = _csv([("2025-03-01", 1.5), ("2025-04-01", 1.5)])
    client = _FakeClient({"VIXCLS": vix, "BAA10Y": flat, "T10Y3M": flat})

    reg = regime_as_of(dt.date(2025, 4, 1), client=client)  # strict < 2025-04-01
    assert reg.vix_60d_sma == 15.0  # the 04-01/04-02 spikes are NOT visible
    assert reg.regime == "risk-on"


def test_regime_risk_off_when_prior_vix_high() -> None:
    vix = _csv([(f"2025-03-{d:02d}", 40.0) for d in range(1, 28)])
    flat = _csv([("2025-03-01", 1.5)])
    client = _FakeClient({"VIXCLS": vix, "BAA10Y": flat, "T10Y3M": flat})
    reg = regime_as_of(dt.date(2025, 4, 1), client=client)
    assert reg.regime == "risk-off"  # VIX 60d SMA 40 > 35


def test_regime_no_prior_data_defaults_risk_on() -> None:
    empty = _csv([("2025-05-01", 20.0)])  # only data on/after as_of
    client = _FakeClient({"VIXCLS": empty, "BAA10Y": empty, "T10Y3M": empty})
    reg = regime_as_of(dt.date(2025, 4, 1), client=client)
    assert reg.regime == "risk-on"
