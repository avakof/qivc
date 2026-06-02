"""Unit tests for the cluster detector."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from qivc.filters.cluster_detector import dedupe_by_accession, detect_clusters
from qivc.schemas import InsiderTransaction

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _txn(
    cik: str,
    d: date,
    ticker: str = "FCN",
    is_officer: bool = True,
    value_usd: float = 50_000.0,
) -> InsiderTransaction:
    return InsiderTransaction(
        cik=cik,
        name=f"Insider {cik}",
        title="CFO" if is_officer else "Director",
        ticker=ticker,
        shares=1000.0,
        price=value_usd / 1000.0,
        value_usd=value_usd,
        transaction_date=d,
        filed_date=d,
        transaction_code="P",
        is_director=not is_officer,
        is_officer=is_officer,
        is_ten_percent_owner=False,
    )


_BASE_DATE = date(2026, 5, 1)


# ---------------------------------------------------------------------------
# MANDATORY spec test cases
# ---------------------------------------------------------------------------


def test_three_buys_in_seven_days_is_track_a_cluster() -> None:
    """3 opportunistic buys in 7 days from 3 distinct insiders → Track A cluster."""
    txns = [
        _txn("A1", _BASE_DATE + timedelta(days=0)),
        _txn("A2", _BASE_DATE + timedelta(days=3)),
        _txn("A3", _BASE_DATE + timedelta(days=7)),  # exactly at window edge
    ]
    classifications = {"A1": "opportunistic", "A2": "opportunistic", "A3": "opportunistic"}
    clusters = detect_clusters(txns, classifications, window_days=7)

    assert len(clusters) == 1
    assert clusters[0].track == "A"
    assert clusters[0].ticker == "FCN"
    assert len({t.cik for t in clusters[0].transactions}) >= 3


def test_three_buys_spanning_eight_days_is_no_cluster() -> None:
    """3 buys spanning 8 days → window of 7 days cannot contain all 3 → no Track A."""
    txns = [
        _txn("B1", _BASE_DATE + timedelta(days=0)),
        _txn("B2", _BASE_DATE + timedelta(days=4)),
        _txn("B3", _BASE_DATE + timedelta(days=8)),  # day 8 > day 0 + 7 days
    ]
    classifications = {"B1": "opportunistic", "B2": "opportunistic", "B3": "opportunistic"}
    clusters = detect_clusters(txns, classifications, window_days=7)

    # B3 at day 8 is outside the 7-day window anchored at day 0
    # Check each possible anchor:
    # anchor B1 (day 0): window ends day 7; B3 at day 8 → excluded → 2 distinct
    # anchor B2 (day 4): window ends day 11; B1 at day 0 < day 4 → excluded → 2 distinct
    # anchor B3 (day 8): window ends day 15; only B3 in window → 1 distinct
    assert all(c.track != "A" for c in clusters)


def test_two_opportunistic_plus_one_routine_is_no_cluster() -> None:
    """2 opportunistic + 1 routine within 7 days → not enough opportunistic → no Track A."""
    txns = [
        _txn("C1", _BASE_DATE + timedelta(days=0)),
        _txn("C2", _BASE_DATE + timedelta(days=3)),
        _txn("C3", _BASE_DATE + timedelta(days=5)),
    ]
    classifications = {
        "C1": "opportunistic",
        "C2": "opportunistic",
        "C3": "routine",  # ← routine; excluded from Track A count
    }
    clusters = detect_clusters(txns, classifications, window_days=7)
    assert all(c.track != "A" for c in clusters)


# ---------------------------------------------------------------------------
# Track B tests
# ---------------------------------------------------------------------------


def test_track_b_officer_buy_above_threshold() -> None:
    """Single C-suite opportunistic buy ≥ threshold → Track B cluster."""
    txns = [_txn("D1", _BASE_DATE, is_officer=True, value_usd=300_000.0)]
    classifications = {"D1": "opportunistic"}
    clusters = detect_clusters(txns, classifications, track_b_min_usd=250_000.0)

    assert len(clusters) == 1
    assert clusters[0].track == "B"
    assert clusters[0].total_value_usd == pytest.approx(300_000.0)


def test_track_b_below_threshold_no_cluster() -> None:
    txns = [_txn("E1", _BASE_DATE, is_officer=True, value_usd=100_000.0)]
    classifications = {"E1": "opportunistic"}
    clusters = detect_clusters(txns, classifications, track_b_min_usd=250_000.0)
    assert clusters == []


def test_track_b_routine_officer_not_counted() -> None:
    txns = [_txn("F1", _BASE_DATE, is_officer=True, value_usd=500_000.0)]
    classifications = {"F1": "routine"}  # routine → excluded
    clusters = detect_clusters(txns, classifications, track_b_min_usd=250_000.0)
    assert clusters == []


def test_track_b_not_officer_no_cluster() -> None:
    txns = [_txn("G1", _BASE_DATE, is_officer=False, value_usd=500_000.0)]
    classifications = {"G1": "opportunistic"}
    clusters = detect_clusters(txns, classifications, track_b_min_usd=250_000.0)
    assert clusters == []


# ---------------------------------------------------------------------------
# Multi-ticker isolation
# ---------------------------------------------------------------------------


def test_cluster_isolated_per_ticker() -> None:
    """Transactions from different tickers do not combine into a cluster."""
    txns = [
        _txn("H1", _BASE_DATE, ticker="TICK1"),
        _txn("H2", _BASE_DATE, ticker="TICK2"),
        _txn("H3", _BASE_DATE, ticker="TICK1"),
    ]
    classifications = {"H1": "opportunistic", "H2": "opportunistic", "H3": "opportunistic"}
    clusters = detect_clusters(txns, classifications, window_days=7)
    # TICK1 has 2 buyers, TICK2 has 1 → neither forms a Track A cluster
    assert all(c.track != "A" for c in clusters)


def test_track_a_preferred_over_track_b() -> None:
    """When both Track A and Track B qualify for same ticker, only Track A is emitted."""
    txns = [
        _txn("I1", _BASE_DATE, is_officer=True, value_usd=1_000_000.0),
        _txn("I2", _BASE_DATE + timedelta(days=1), is_officer=True, value_usd=500_000.0),
        _txn("I3", _BASE_DATE + timedelta(days=2), is_officer=True, value_usd=300_000.0),
    ]
    classifications = {"I1": "opportunistic", "I2": "opportunistic", "I3": "opportunistic"}
    clusters = detect_clusters(txns, classifications, window_days=7)
    tracks = [c.track for c in clusters]
    assert "A" in tracks
    assert tracks.count("A") == 1


def test_empty_inputs_returns_empty() -> None:
    assert detect_clusters([], {}) == []


def test_total_value_usd_summed_correctly() -> None:
    txns = [
        _txn("J1", _BASE_DATE, value_usd=100_000.0),
        _txn("J2", _BASE_DATE + timedelta(days=1), value_usd=200_000.0),
        _txn("J3", _BASE_DATE + timedelta(days=2), value_usd=150_000.0),
    ]
    classifications = {"J1": "opportunistic", "J2": "opportunistic", "J3": "opportunistic"}
    clusters = detect_clusters(txns, classifications, window_days=7)
    assert len(clusters) == 1
    assert clusters[0].total_value_usd == pytest.approx(450_000.0)


# ---------------------------------------------------------------------------
# Fan-out dedup policy (Task 8.2): co-filers on one filing = one event
# ---------------------------------------------------------------------------


def _txn_acc(
    cik: str,
    accession: str,
    d: date = _BASE_DATE,
    is_officer: bool = True,
    value_usd: float = 50_000.0,
) -> InsiderTransaction:
    return InsiderTransaction(
        cik=cik,
        name=f"Insider {cik}",
        title="CFO" if is_officer else "Director",
        ticker="FCN",
        shares=1000.0,
        price=value_usd / 1000.0,
        value_usd=value_usd,
        transaction_date=d,
        filed_date=d,
        transaction_code="P",
        is_director=not is_officer,
        is_officer=is_officer,
        is_ten_percent_owner=False,
        accession_number=accession,
    )


def test_dedupe_collapses_co_owners_on_one_accession() -> None:
    """3 co-owners on ONE filing collapse to a single representative record."""
    txns = [
        _txn_acc("C1", "ACC-1", is_officer=False, value_usd=10_000.0),
        _txn_acc("C2", "ACC-1", is_officer=True, value_usd=20_000.0),
        _txn_acc("C3", "ACC-1", is_officer=False, value_usd=99_000.0),
    ]
    out = dedupe_by_accession(txns)
    assert len(out) == 1
    # Officer is preferred over larger non-officer value.
    assert out[0].cik == "C2"


def test_dedupe_preserves_distinct_accessions() -> None:
    txns = [_txn_acc("C1", "ACC-1"), _txn_acc("C2", "ACC-2"), _txn_acc("C3", "ACC-3")]
    assert len(dedupe_by_accession(txns)) == 3


def test_dedupe_passes_through_empty_accession() -> None:
    """Records with no accession (live single-owner / synthetic) are never merged."""
    txns = [_txn_acc("C1", ""), _txn_acc("C2", ""), _txn_acc("C3", "ACC-9")]
    out = dedupe_by_accession(txns)
    assert len(out) == 3
    assert {t.cik for t in out} == {"C1", "C2", "C3"}


def test_co_owner_filing_does_not_inflate_track_a() -> None:
    """
    THE acceptance test: a single filing with 3 opportunistic co-owners must
    contribute ONE buyer, not three — so it cannot by itself trip Track A
    (which needs >=3 DISTINCT filings/insiders).
    """
    co_owners = [
        _txn_acc("C1", "ACC-SAME", d=_BASE_DATE, is_officer=False),
        _txn_acc("C2", "ACC-SAME", d=_BASE_DATE, is_officer=False),
        _txn_acc("C3", "ACC-SAME", d=_BASE_DATE, is_officer=False),
    ]
    classifications = {"C1": "opportunistic", "C2": "opportunistic", "C3": "opportunistic"}

    # Without dedup, the raw fan-out would look like 3 distinct buyers -> false Track A.
    raw = detect_clusters(co_owners, classifications, window_days=7)
    assert any(c.track == "A" for c in raw), "raw fan-out spuriously trips Track A"

    # With dedup, the single filing is one event -> no Track A.
    deduped = detect_clusters(dedupe_by_accession(co_owners), classifications, window_days=7)
    assert not any(c.track == "A" for c in deduped)


def test_three_distinct_filings_still_trip_track_a_after_dedup() -> None:
    """Dedup must NOT suppress a genuine 3-distinct-filing cluster."""
    txns = [
        _txn_acc("D1", "ACC-1", d=_BASE_DATE, is_officer=False),
        _txn_acc("D2", "ACC-2", d=_BASE_DATE + timedelta(days=2), is_officer=False),
        _txn_acc("D3", "ACC-3", d=_BASE_DATE + timedelta(days=4), is_officer=False),
    ]
    classifications = {"D1": "opportunistic", "D2": "opportunistic", "D3": "opportunistic"}
    clusters = detect_clusters(dedupe_by_accession(txns), classifications, window_days=7)
    assert any(c.track == "A" for c in clusters)
