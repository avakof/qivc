"""
Local live-server mode for the paper-trade dashboard (Task 15.1).

`qivc dashboard --serve` runs a stdlib http.server bound to 127.0.0.1 ONLY (never
network-exposed). On EVERY request it re-reads the qivc paper ledger fresh and
re-builds the dashboard from scratch (re-marking open positions at current prices,
respecting the YFinancePriceProvider's ~15-min TTL), then serves the rendered HTML.
So a browser refresh = current ledger + current marks, no restart. Dependency-light
(stdlib only). The one-shot write-file mode is unchanged.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from qivc.dashboard.build import PriceProvider, build_dashboard_data
from qivc.dashboard.detail import ProfileFetch, build_ticker_detail
from qivc.dashboard.render import render_detail_html, render_html

LOCALHOST = "127.0.0.1"


def make_handler(
    ledger: str,
    config: str,
    *,
    price_provider: PriceProvider,
    today_fn: Callable[[], _dt.date],
    delisting_lookup: Callable[[str], Any] | None,
    db_path: str,
    profile_fetch: ProfileFetch | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Build a request handler that re-reads the ledger + re-marks prices per request."""

    def _send(handler: BaseHTTPRequestHandler, body: bytes, status: int = 200) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path.startswith("/t/"):  # ticker drill-down
                ticker = path[3:].strip("/").upper()
                detail = build_ticker_detail(
                    ledger, config, ticker, db_path=db_path, today=today_fn(),
                    profile_fetch=profile_fetch, price_provider=price_provider,
                )
                _send(self, render_detail_html(detail).encode("utf-8"))
                return
            if path not in ("/", "/index.html"):
                _send(self, b"not found", status=404)
                return
            # Fresh read + fresh marks on EVERY request (never serve stale derived data).
            data = build_dashboard_data(
                ledger, config, price_provider=price_provider,
                today=today_fn(), delisting_lookup=delisting_lookup,
            )
            _send(self, render_html(data, detail_links=True).encode("utf-8"))

        def log_message(self, *args: Any) -> None:  # keep the console quiet
            return

    return _Handler


def create_server(
    ledger: str,
    config: str,
    *,
    port: int,
    price_provider: PriceProvider,
    host: str = LOCALHOST,
    today_fn: Callable[[], _dt.date] | None = None,
    delisting_lookup: Callable[[str], Any] | None = None,
    db_path: str = "data/duckdb/qivc.db",
    profile_fetch: ProfileFetch | None = None,
) -> HTTPServer:
    """
    Bind an HTTPServer on *host:port* (127.0.0.1 only by default). Raises OSError
    if the port is unavailable. Does NOT start serving — call serve_forever().
    """
    handler = make_handler(
        ledger, config, price_provider=price_provider,
        today_fn=today_fn or _dt.date.today, delisting_lookup=delisting_lookup,
        db_path=db_path, profile_fetch=profile_fetch,
    )
    return HTTPServer((host, port), handler)
