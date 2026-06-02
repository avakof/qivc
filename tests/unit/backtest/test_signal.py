"""Unit tests for the PIT full-signal (Task 10) — focus on no-look-ahead."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from qivc.backtest.signal import qivc_signal_as_of
from qivc.schemas import Fundamentals, Liquidity, MarketRegime
from qivc.storage.db import get_connection

_RISK_ON = MarketRegime(
    vix_60d_sma=18.0,
    credit_spread_bps=150.0,
    yield_curve_bps=30.0,
    value_growth_12m=0.0,
    regime="risk-on",
)


def _good_fundamentals(ticker: str, as_of: dt.date) -> Fundamentals:
    # 9/9-ish F-Score, GP/A 0.4 — passes the quality gates.
    return Fundamentals(
        ticker=ticker,
        fiscal_period="PIT",
        roa=0.10,
        ocf=5e6,
        delta_roa=0.02,
        ocf_gt_ni=True,
        delta_leverage=-0.05,
        delta_liquidity=0.2,
        no_share_issuance=True,
        delta_gross_margin=0.03,
        delta_asset_turnover=0.05,
        gross_profit=1.2e9,
        total_assets=3e9,
    )


def _good_liquidity(ticker: str, as_of: dt.date) -> Liquidity:
    return Liquidity(
        ticker=ticker,
        market_cap_usd=2e9,
        adv_20d_usd=5e7,
        next_earnings_date=None,
        has_pending_ma=False,
    )


def _insert(conn, cik, ticker, filed, *, officer=True, value=300_000.0, acc=None):
    acc = acc or f"ACC-{cik}-{filed}"
    shares = value / 10.0
    conn.execute(
        """INSERT INTO form4_historical (cik,name,title,ticker,shares,price,value_usd,
           transaction_date,filed_date,transaction_code,is_director,is_officer,
           is_ten_percent_owner,accession_number,source_quarter)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            cik,
            f"Ins {cik}",
            "CEO" if officer else "Director",
            ticker,
            shares,
            10.0,
            value,
            filed,
            filed,
            "P",
            not officer,
            officer,
            False,
            acc,
            "x",
        ],
    )


def _store(tmp_path: Path, rows: list[tuple]) -> str:
    db = str(tmp_path / "bt.db")
    now = dt.datetime(2026, 1, 1)
    with get_connection(db) as conn:
        for q in ("2022q1", "2025q4"):
            conn.execute("INSERT INTO bulk_load_progress VALUES (?,?,?,?)", [q, now, 0, "complete"])
        for r in rows:
            _insert(conn, *r)
        conn.commit()
    return db


def _signal(db, as_of, universe, **kw):
    return qivc_signal_as_of(
        as_of,
        db_path=db,
        universe=universe,
        sector_map=dict.fromkeys(universe, "Industrials"),
        regime=_RISK_ON,
        fundamentals_provider=_good_fundamentals,
        liquidity_provider=_good_liquidity,
        industry_provider=lambda t: "Machinery",
        **kw,
    )


def test_track_b_candidate_with_three_prior_years(tmp_path: Path) -> None:
    """An opportunistic C-suite buyer (>=3 prior years, no May pattern) -> Track B candidate."""
    as_of = dt.date(2025, 5, 15)
    rows = [
        # prior history in 2022/2023/2024 (Feb) -> 3 distinct prior years, no May -> opportunistic
        ("CIK1", "AAA", dt.date(2022, 2, 1)),
        ("CIK1", "AAA", dt.date(2023, 2, 1)),
        ("CIK1", "AAA", dt.date(2024, 2, 1)),
        # the cluster-window buy (filed within 14d before as_of), >= $250k officer
        ("CIK1", "AAA", dt.date(2025, 5, 5)),
    ]
    res = _signal(_store(tmp_path, rows), as_of, {"AAA"})
    assert res.n_clustered_tickers == 1
    assert [h.ticker for h in res.holdings] == ["AAA"]
    assert res.holdings[0].track == "B"
    assert 0.0 < res.holdings[0].weight <= 0.10
    assert res.cash_weight == 1.0 - res.holdings[0].weight


def test_no_lookahead_future_filing_excluded(tmp_path: Path) -> None:
    """A filing filed ON/after as_of must never enter the signal (strict PIT)."""
    as_of = dt.date(2025, 5, 15)
    rows = [
        ("CIK1", "AAA", dt.date(2022, 2, 1)),
        ("CIK1", "AAA", dt.date(2023, 2, 1)),
        ("CIK1", "AAA", dt.date(2024, 2, 1)),
        ("CIK1", "AAA", as_of),  # filed exactly on as_of -> excluded
    ]
    res = _signal(_store(tmp_path, rows), as_of, {"AAA"})
    # No cluster-window buy strictly before as_of -> no cluster.
    assert res.n_clustered_tickers == 0
    assert res.holdings == []


def test_universe_restriction(tmp_path: Path) -> None:
    """A clustered ticker outside the IWM universe is ignored."""
    as_of = dt.date(2025, 5, 15)
    rows = [
        ("CIK1", "ZZZ", dt.date(2022, 2, 1)),
        ("CIK1", "ZZZ", dt.date(2023, 2, 1)),
        ("CIK1", "ZZZ", dt.date(2024, 2, 1)),
        ("CIK1", "ZZZ", dt.date(2025, 5, 5)),
    ]
    res = _signal(_store(tmp_path, rows), as_of, {"AAA"})  # ZZZ not in universe
    assert res.n_clustered_tickers == 0


def test_insufficient_history_is_unclassified_no_cluster(tmp_path: Path) -> None:
    """Only 2 distinct prior years -> unclassified -> no cluster (the 2025/2022-gap case)."""
    as_of = dt.date(2025, 5, 15)
    rows = [
        ("CIK1", "AAA", dt.date(2023, 2, 1)),  # only 2023 + 2024 -> 2 distinct prior years
        ("CIK1", "AAA", dt.date(2024, 2, 1)),
        ("CIK1", "AAA", dt.date(2025, 5, 5)),
    ]
    res = _signal(_store(tmp_path, rows), as_of, {"AAA"})
    assert res.n_clustered_tickers == 0


def test_regime_block_holds_cash(tmp_path: Path) -> None:
    risk_off = _RISK_ON.model_copy(update={"regime": "risk-off", "vix_60d_sma": 40.0})
    res = qivc_signal_as_of(
        dt.date(2025, 5, 15),
        db_path=_store(tmp_path, []),
        universe={"AAA"},
        sector_map={"AAA": "Industrials"},
        regime=risk_off,
        fundamentals_provider=_good_fundamentals,
        liquidity_provider=_good_liquidity,
        industry_provider=lambda t: "x",
    )
    assert res.regime_blocked is True
    assert res.cash_weight == 1.0
    assert res.holdings == []
