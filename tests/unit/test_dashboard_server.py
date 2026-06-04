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


def _two_config_registry(tmp_path: Path):  # type: ignore[no-untyped-def]
    from qivc.dashboard.configs import PaperConfig

    led90 = tmp_path / "l.jsonl"
    led14 = tmp_path / "l_w14.jsonl"
    _write(led90, [_rec("2026-04-20", ["AAOI", "TYRA"])])      # 90-day holds AAOI, TYRA
    _write(led14, [_rec("2026-05-20", ["AAOI"])])              # 14-day holds only AAOI
    return {
        "v3.0": PaperConfig("v3.0", str(led90), _CONFIG, _CONFIG, 90,
                            "v3.0 · 90-day (baseline)", False),
        "v3.0-w14": PaperConfig("v3.0-w14", str(led14), _CONFIG, _CONFIG, 14,
                                "v3.0-w14 · 14-day (untested fork)", True),
    }


def test_multi_config_selector_and_compare_routes(tmp_path: Path) -> None:
    cfgs = _two_config_registry(tmp_path)
    httpd = create_server(
        port=0, price_provider=FakePrices(), today_fn=lambda: _TODAY,
        configs=cfgs, default_key="v3.0",
    )
    port = httpd.server_address[1]
    _serve(httpd)
    try:
        # default config page shows the selector with both configs (labels are in the
        # embedded JSON; the unicode middot is ·-escaped there, rendered client-side)
        _, home = _get(port)
        assert "14-day (untested fork)" in home
        assert "compare" in home
        # switch to the experimental config
        with urllib.request.urlopen(f"http://{LOCALHOST}:{port}/?config=v3.0-w14") as r:
            w14 = r.read().decode()
        assert r.status == 200 and "untested fork" in w14
        # comparison view: held-ticker difference (90 holds TYRA, 14 doesn't)
        with urllib.request.urlopen(f"http://{LOCALHOST}:{port}/compare") as r:
            cmp_html = r.read().decode()
        assert "Forward A/B" in cmp_html
        assert "Held-ticker difference" in cmp_html
        assert "TYRA" in cmp_html                         # only-90-day name appears in the diff
        assert "not validated" in cmp_html.lower()        # banner permanent
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_compare_held_diff_render() -> None:
    from qivc.dashboard.render import render_compare_html

    def _data(opens):  # type: ignore[no-untyped-def]
        return {"meta": {}, "open": [{"ticker": t} for t in opens],
                "headline": {"A": {"ret": 0, "bench": 0, "sub": ""},
                             "B": {"ret": 1.0, "bench": 0.5, "sub": ""},
                             "n": 2, "nLabel": "void", "conc": 50, "r2": None},
                "equity": []}
    items = [
        {"key": "v3.0", "label": "90-day", "experimental": False, "data": _data(["AAA", "BBB"])},
        {"key": "v3.0-w14", "label": "14-day", "experimental": True, "data": _data(["BBB", "CCC"])},
    ]
    html = render_compare_html(items)
    assert "both hold:</b> BBB" in html
    assert "only 90-day:</b> AAA" in html
    assert "only 14-day:</b> CCC" in html


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
