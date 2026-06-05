"""Resilient regime fetch — retry + neutral fallback (Task 15.2)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest

from qivc.backtest import pit_regime

_AS_OF = dt.date(2026, 6, 4)


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_fred_fetch_retries_on_timeout_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pit_regime.time, "sleep", lambda _s: None)  # no real backoff wait
    calls = {"n": 0}

    class Client:
        def get(self, url: str) -> _Resp:
            calls["n"] += 1
            if calls["n"] < 3:  # fail the first two attempts
                raise httpx.ReadTimeout("FRED slow")
            return _Resp("DATE,VIXCLS\n2026-05-01,18.0\n2026-05-02,19.0\n")

    vals = pit_regime._fred_as_of(Client(), "VIXCLS", _AS_OF)  # type: ignore[arg-type]
    assert calls["n"] == 3            # retried twice, succeeded on the third
    assert vals == [18.0, 19.0]


def test_fred_fetch_exhausts_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pit_regime.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    class Client:
        def get(self, url: str) -> _Resp:
            calls["n"] += 1
            raise httpx.ConnectError("FRED down")

    with pytest.raises(httpx.TransportError):
        pit_regime._fred_as_of(Client(), "VIXCLS", _AS_OF)  # type: ignore[arg-type]
    assert calls["n"] == len(pit_regime._FRED_BACKOFF) + 1  # all attempts used


def test_regime_resilient_falls_back_to_neutral_no_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pit_regime.time, "sleep", lambda _s: None)

    class Client:
        def get(self, url: str) -> _Resp:
            raise httpx.ReadTimeout("FRED unreachable")

        def close(self) -> None:
            return None

    # no yf_fetch -> neutral fallback
    regime, source = pit_regime.regime_as_of_resilient(_AS_OF, client=Client())  # type: ignore[arg-type]
    assert source == "neutral_fallback"
    assert regime is pit_regime.NEUTRAL_REGIME
    assert regime.regime == "risk-on"     # neutral = no spurious risk-off block


def test_regime_resilient_reports_fred_live_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pit_regime, "regime_as_of", lambda as_of, client=None: pit_regime.NEUTRAL_REGIME
    )
    _regime, source = pit_regime.regime_as_of_resilient(_AS_OF)
    assert source == "fred_live"          # FRED succeeded (even if data was sparse)


def _yf(vix: float, tnx: float = 4.2, irx: float = 5.0):  # type: ignore[no-untyped-def]
    """A yfinance-closes fetch with constant ^VIX/^TNX/^IRX over 80 pre-as_of days."""
    dates = [(_AS_OF - dt.timedelta(days=i + 1)).isoformat() for i in range(80)]

    def fetch(tickers, start, end):  # type: ignore[no-untyped-def]
        return {"^VIX": {d: vix for d in dates}, "^TNX": {d: tnx for d in dates},
                "^IRX": {d: irx for d in dates}}
    return fetch


def test_regime_from_yfinance_is_vix_led() -> None:
    # calm VIX -> risk-on; yield curve = (4.2 - 5.0)*100 = -80 bps (inverted, secondary)
    r = pit_regime.regime_from_yfinance(_AS_OF, _yf(18.0))
    assert r is not None
    assert r.regime == "risk-on" and r.vix_60d_sma == 18.0
    assert r.yield_curve_bps == pytest.approx(-80.0)
    assert r.credit_spread_bps == 0.0      # omitted in the yfinance fallback
    # elevated VIX -> risk-mid; very high -> risk-off
    assert pit_regime.regime_from_yfinance(_AS_OF, _yf(30.0)).regime == "risk-mid"  # type: ignore[union-attr]
    assert pit_regime.regime_from_yfinance(_AS_OF, _yf(40.0)).regime == "risk-off"  # type: ignore[union-attr]


def test_regime_from_yfinance_none_without_vix() -> None:
    assert pit_regime.regime_from_yfinance(_AS_OF, lambda t, s, e: {}) is None


def test_resilient_uses_yfinance_fallback_when_fred_down() -> None:
    class Client:
        def get(self, url: str) -> _Resp:
            raise httpx.ReadTimeout("FRED unreachable")

        def close(self) -> None:
            return None

    reg, source = pit_regime.regime_as_of_resilient(
        _AS_OF, client=Client(), yf_fetch=_yf(40.0)  # type: ignore[arg-type]
    )
    assert source == "yfinance_fallback"
    assert reg.regime == "risk-off"        # a REAL VIX-derived regime, not neutral
    assert reg is not pit_regime.NEUTRAL_REGIME


def test_resilient_neutral_when_fred_and_yfinance_both_fail() -> None:
    class Client:
        def get(self, url: str) -> _Resp:
            raise httpx.ReadTimeout("FRED unreachable")

        def close(self) -> None:
            return None

    def yf_boom(tickers, start, end):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("yahoo unreachable")

    reg, source = pit_regime.regime_as_of_resilient(
        _AS_OF, client=Client(), yf_fetch=yf_boom  # type: ignore[arg-type]
    )
    assert source == "neutral_fallback" and reg is pit_regime.NEUTRAL_REGIME


def test_regime_source_recorded_in_ledger(tmp_path: Path) -> None:
    from qivc.backtest.portfolio_constructor import Position
    from qivc.paper.forward import append_record, build_record, read_last_record
    from qivc.schemas import MarketRegime

    reg = MarketRegime(
        vix_60d_sma=0.0, credit_spread_bps=0.0, yield_curve_bps=0.0,
        value_growth_12m=0.0, regime="risk-on",
    )
    pos = [Position("AAA", "Energy", 1.0, dt.date(2026, 6, 4), 0.7)]
    rec = build_record(
        dt.date(2026, 6, 4), "N10_thr90_hold30", reg, pos, 5,
        {"AAA": {"insider": 0.8}}, regime_source="yfinance_fallback",
    )
    assert rec.regime_source == "yfinance_fallback"
    led = tmp_path / "ledger.jsonl"
    append_record(led, rec)
    # round-trips through the JSONL ledger
    back = read_last_record(led, "N10_thr90_hold30")
    assert back is not None and back.regime_source == "yfinance_fallback"
