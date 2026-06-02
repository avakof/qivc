"""Unit tests for cached PIT providers (Task 10) — no network (cache-hit paths)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from qivc.backtest import providers
from qivc.schemas import Fundamentals


def test_cache_json_roundtrip(tmp_path: Path) -> None:
    c = providers.BacktestCache(tmp_path / "c")
    assert c.get_json("missing") is None
    c.put_json("x", {"a": 1})
    assert c.get_json("x") == {"a": 1}


def _prices(tmp_path: Path) -> providers.BacktestCache:
    c = providers.BacktestCache(tmp_path / "c")
    idx = pd.bdate_range("2025-01-01", "2025-02-28")
    close = pd.DataFrame({"AAA": range(10, 10 + len(idx))}, index=idx, dtype=float)
    vol = pd.DataFrame({"AAA": [1_000_000] * len(idx)}, index=idx, dtype=float)
    close.to_csv(c.dir / "prices.csv")
    vol.to_csv(c.dir / "volume.csv")
    return c


def test_build_price_cache_hits_existing(tmp_path: Path) -> None:
    c = _prices(tmp_path)
    # cache present -> returns without network
    out = providers.build_price_cache(c, ["AAA"], dt.date(2025, 1, 1), dt.date(2025, 2, 28))
    assert "AAA" in out.columns


def test_load_prices_roundtrip(tmp_path: Path) -> None:
    c = _prices(tmp_path)
    close, vol = providers.load_prices(c)
    assert "AAA" in close.columns
    assert "AAA" in vol.columns


def test_fetch_info_cache_hit(tmp_path: Path) -> None:
    c = providers.BacktestCache(tmp_path / "c")
    c.put_json(
        "info", {"AAA": {"shares_outstanding": 1e8, "industry": "Banks", "sector": "Financials"}}
    )
    info = providers.fetch_info_cache(c, ["AAA"])  # cached -> no network
    assert info["AAA"]["industry"] == "Banks"


def test_liquidity_provider_pit(tmp_path: Path) -> None:
    c = _prices(tmp_path)
    close, vol = providers.load_prices(c)
    info = {"AAA": {"shares_outstanding": 1e8, "industry": "Banks", "sector": "Financials"}}
    prov = providers.make_liquidity_provider(close, vol, info)
    as_of = dt.date(2025, 2, 1)
    liq = prov("AAA", as_of)
    assert liq is not None
    # market cap uses last price strictly before as_of
    px_before = close["AAA"].loc[close.index < pd.Timestamp(as_of)].iloc[-1]
    assert abs(liq.market_cap_usd - px_before * 1e8) < 1.0
    assert liq.adv_20d_usd > 0
    assert prov("ZZZ", as_of) is None  # unknown ticker


def test_industry_provider(tmp_path: Path) -> None:
    prov = providers.make_industry_provider({"AAA": {"industry": "Banks"}})
    assert prov("AAA") == "Banks"
    assert prov("ZZZ") == "Unknown"


def test_fundamentals_provider_from_cache(tmp_path: Path) -> None:
    c = providers.BacktestCache(tmp_path / "c")
    fund = Fundamentals(
        ticker="AAA",
        fiscal_period="PIT",
        roa=0.1,
        ocf=1e6,
        delta_roa=0.0,
        ocf_gt_ni=True,
        delta_leverage=0.0,
        delta_liquidity=0.0,
        no_share_issuance=True,
        delta_gross_margin=0.0,
        delta_asset_turnover=0.0,
        gross_profit=5e8,
        total_assets=2e9,
    )
    c.put_json("fundamentals", {"AAA|2025-04-01": json.loads(fund.model_dump_json())})
    prov = providers.make_fundamentals_provider(c)
    got = prov("AAA", dt.date(2025, 4, 1))
    assert got is not None
    assert got.roa == 0.1
    assert prov("AAA", dt.date(2025, 5, 1)) is None  # no record for that date
