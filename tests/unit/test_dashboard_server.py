"""Tests for the dashboard local live-server mode (Task 15.1)."""

from __future__ import annotations

import datetime as dt
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from qivc.dashboard.server import LOCALHOST, create_server

_TODAY = dt.date(2026, 6, 4)
_CONFIG = "N10_thr90_hold30"
_PRICES = {
    "AAOI": {"2026-04-20": 24.0, "2026-06-03": 26.4},   # open +10%
    "TYRA": {"2026-05-20": 10.0, "2026-06-03": 11.0},
    "IWN": {"2026-04-20": 100.0, "2026-04-27": 101.0, "2026-05-20": 103.0, "2026-06-03": 105.0},
    "^IRX": {"2026-04-20": 5.0, "2026-06-03": 5.0},
}


class FakePrices:
    def daily_closes(self, tickers, start, end):  # type: ignore[no-untyped-def]
        return {t: dict(_PRICES.get(t, {})) for t in tickers}


def _rec(as_of: str, tickers: list[str]) -> dict:
    fac = {"insider": 0.85, "quality": 0.5, "valuation": 0.55, "momentum": 0.5, "technical": 0.5}
    return {
        "as_of": as_of, "config": _CONFIG, "regime": "risk-on",
        "equity_pct": 100.0, "n_scored": 30,
        "positions": [
            {"ticker": t, "sector": "Information Technology", "weight": round(1 / len(tickers), 4),
             "composite": 0.71, "entry_date": as_of, "components": fac}
            for t in tickers
        ],
    }


def _write(path: Path, recs: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def _serve(httpd):  # type: ignore[no-untyped-def]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t


def _get(port: int) -> tuple[int, str]:
    with urllib.request.urlopen(f"http://{LOCALHOST}:{port}/", timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


def test_server_binds_localhost_and_serves_200(tmp_path: Path) -> None:
    led = tmp_path / "l.jsonl"
    _write(led, [_rec("2026-04-20", ["AAOI"])])
    httpd = create_server(
        str(led), _CONFIG, port=0, price_provider=FakePrices(), today_fn=lambda: _TODAY
    )
    assert httpd.server_address[0] == "127.0.0.1"   # localhost only, never 0.0.0.0
    port = httpd.server_address[1]
    _serve(httpd)
    try:
        status, body = _get(port)
        assert status == 200
        assert "not validated" in body.lower()       # permanent banner
        assert "AAOI" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_request_re_reads_ledger_live(tmp_path: Path) -> None:
    """Writing a new entry then re-requesting shows it — proves live re-read."""
    led = tmp_path / "l.jsonl"
    _write(led, [_rec("2026-04-20", ["AAOI"])])
    httpd = create_server(
        str(led), _CONFIG, port=0, price_provider=FakePrices(), today_fn=lambda: _TODAY
    )
    port = httpd.server_address[1]
    _serve(httpd)
    try:
        _, body1 = _get(port)
        assert "TYRA" not in body1
        # a new signal is written to the ledger AFTER the first load
        _write(led, [_rec("2026-04-20", ["AAOI"]), _rec("2026-05-20", ["AAOI", "TYRA"])])
        _, body2 = _get(port)
        assert "TYRA" in body2                       # picked up without restart
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_open_marks_served(tmp_path: Path) -> None:
    led = tmp_path / "l.jsonl"
    _write(led, [_rec("2026-04-20", ["AAOI"])])
    httpd = create_server(
        str(led), _CONFIG, port=0, price_provider=FakePrices(), today_fn=lambda: _TODAY
    )
    port = httpd.server_address[1]
    _serve(httpd)
    try:
        _, body = _get(port)
        # AAOI open, live-marked 26.4 (the most recent close)
        assert "26.4" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_empty_ledger_served(tmp_path: Path) -> None:
    httpd = create_server(
        str(tmp_path / "none.jsonl"), _CONFIG, port=0,
        price_provider=FakePrices(), today_fn=lambda: _TODAY,
    )
    port = httpd.server_address[1]
    _serve(httpd)
    try:
        status, body = _get(port)
        assert status == 200
        assert "accumulating forward data" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_port_in_use_raises_oserror(tmp_path: Path) -> None:
    led = tmp_path / "l.jsonl"
    _write(led, [_rec("2026-04-20", ["AAOI"])])
    first = create_server(str(led), _CONFIG, port=0, price_provider=FakePrices())
    taken = first.server_address[1]
    try:
        with pytest.raises(OSError):
            create_server(str(led), _CONFIG, port=taken, price_provider=FakePrices())
    finally:
        first.server_close()


def test_auto_refresh_control_present(tmp_path: Path) -> None:
    led = tmp_path / "l.jsonl"
    _write(led, [_rec("2026-04-20", ["AAOI"])])
    httpd = create_server(
        str(led), _CONFIG, port=0, price_provider=FakePrices(), today_fn=lambda: _TODAY
    )
    port = httpd.server_address[1]
    _serve(httpd)
    try:
        _, body = _get(port)
        assert 'id="refreshToggle"' in body
        assert 'id="lastUpdated"' in body
        assert "auto-refresh" in body
    finally:
        httpd.shutdown()
        httpd.server_close()
