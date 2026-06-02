"""Unit tests for the committed IWM universe snapshot + loader (Task 10)."""

from __future__ import annotations

from qivc.backtest.universe import (
    UNIVERSE_AS_OF,
    iwm_sector_map,
    iwm_tickers,
    load_iwm_constituents,
)


def test_load_constituents_snapshot() -> None:
    cs = load_iwm_constituents()
    assert len(cs) > 1500  # ~1,905 equities
    assert all(c.ticker for c in cs)
    assert all(c.sector for c in cs)
    assert UNIVERSE_AS_OF == "2026-06-01"


def test_iwm_tickers_is_set() -> None:
    t = iwm_tickers()
    assert isinstance(t, set)
    assert len(t) == len(load_iwm_constituents())  # no dupes
    # a couple of names known to be in the snapshot
    assert "BRT" in t
    assert "ATLO" in t


def test_sector_map_covers_all() -> None:
    smap = iwm_sector_map()
    assert set(smap) == iwm_tickers()
    assert smap["BRT"]  # has a sector label
