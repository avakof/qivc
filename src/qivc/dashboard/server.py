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
from urllib.parse import parse_qs, urlparse

from qivc.dashboard.build import PriceProvider, build_dashboard_data
from qivc.dashboard.configs import PaperConfig
from qivc.dashboard.detail import ProfileFetch, build_ticker_detail
from qivc.dashboard.render import render_compare_html, render_detail_html, render_html

LOCALHOST = "127.0.0.1"


def _selector_meta(configs: dict[str, PaperConfig], selected: str) -> list[dict[str, Any]]:
    return [
        {"key": c.key, "label": c.label, "experimental": c.experimental,
         "selected": c.key == selected}
        for c in configs.values()
    ]


def make_handler(
    configs: dict[str, PaperConfig],
    default_key: str,
    *,
    price_provider: PriceProvider,
    today_fn: Callable[[], _dt.date],
    delisting_lookup: Callable[[str], Any] | None,
    db_path: str,
    profile_fetch: ProfileFetch | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Request handler: re-reads the selected config's ledger + re-marks per request."""
    multi = len(configs) > 1

    def _send(handler: BaseHTTPRequestHandler, body: bytes, status: int = 200) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def _data(cfg: PaperConfig, selected: str) -> dict[str, Any]:
        data = build_dashboard_data(
            cfg.ledger, cfg.config, price_provider=price_provider,
            today=today_fn(), delisting_lookup=delisting_lookup,
        )
        if multi:
            data["meta"]["configs"] = _selector_meta(configs, selected)
            data["meta"]["config_label"] = cfg.label
            data["meta"]["experimental"] = cfg.experimental
        return data

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            u = urlparse(self.path)
            path, qs = u.path, parse_qs(u.query)
            key = qs.get("config", [default_key])[0]
            if key not in configs:
                key = default_key
            cfg = configs[key]

            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path == "/compare" and multi:
                items = [
                    {"key": c.key, "label": c.label, "experimental": c.experimental,
                     "data": _data(c, c.key)}
                    for c in configs.values()
                ]
                _send(self, render_compare_html(items).encode("utf-8"))
                return
            if path.startswith("/t/"):  # ticker drill-down (config-aware window)
                ticker = path[3:].strip("/").upper()
                detail = build_ticker_detail(
                    cfg.ledger, cfg.config, ticker, db_path=db_path, today=today_fn(),
                    profile_fetch=profile_fetch, price_provider=price_provider,
                    ohlcv_fetch=getattr(price_provider, "daily_ohlcv", None),
                    window_days=cfg.window,
                )
                _send(self, render_detail_html(detail).encode("utf-8"))
                return
            if path not in ("/", "/index.html"):
                _send(self, b"not found", status=404)
                return
            _send(self, render_html(_data(cfg, key), detail_links=True).encode("utf-8"))

        def log_message(self, *args: Any) -> None:  # keep the console quiet
            return

    return _Handler


def create_server(
    ledger: str | None = None,
    config: str | None = None,
    *,
    port: int,
    price_provider: PriceProvider,
    host: str = LOCALHOST,
    today_fn: Callable[[], _dt.date] | None = None,
    delisting_lookup: Callable[[str], Any] | None = None,
    db_path: str = "data/duckdb/qivc.db",
    profile_fetch: ProfileFetch | None = None,
    configs: dict[str, PaperConfig] | None = None,
    default_key: str | None = None,
) -> HTTPServer:
    """
    Bind an HTTPServer on *host:port* (127.0.0.1 only by default). Raises OSError if
    the port is unavailable. Pass *configs* for multi-config mode (selector +
    /compare), or a single (ledger, config) for one-config mode.
    """
    if configs is None:
        assert ledger is not None and config is not None
        configs = {
            "_": PaperConfig(
                key="_", ledger=ledger, config=config, grid_config=config,
                window=90, label=config, experimental=False,
            )
        }
        default_key = "_"
    handler = make_handler(
        configs, default_key or next(iter(configs)),
        price_provider=price_provider, today_fn=today_fn or _dt.date.today,
        delisting_lookup=delisting_lookup, db_path=db_path, profile_fetch=profile_fetch,
    )
    return HTTPServer((host, port), handler)
