"""Unit tests for the conviction scorer — table-driven, all boundaries."""

from __future__ import annotations

import pytest

from qivc.schemas import ClusterIntensity, InsiderTrackRecord
from qivc.synthesis.conviction import score, size_for_score


def _intensity(
    distinct: int = 1, csuite: bool = False, ceo: bool = False, cfo: bool = False
) -> ClusterIntensity:
    return ClusterIntensity(distinct_insiders=distinct, has_csuite=csuite, has_ceo=ceo, has_cfo=cfo)


# ---------------------------------------------------------------------------
# F-Score dimension
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fscore, expected",
    [(6, 0), (7, 0), (8, 2), (9, 3)],
)
def test_fscore_points(fscore: int, expected: int) -> None:
    cs = score(fscore, None, 0.0, _intensity())
    assert cs.breakdown["f_score"] == expected


# ---------------------------------------------------------------------------
# Insider track record dimension
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tr, expected",
    [
        (None, 0),
        (InsiderTrackRecord(prior_buys=1, positive_12m=True), 0),
        (InsiderTrackRecord(prior_buys=2, positive_12m=True), 2),
        (InsiderTrackRecord(prior_buys=3, positive_12m=True), 2),
        (InsiderTrackRecord(prior_buys=4, positive_12m=True), 3),
        (InsiderTrackRecord(prior_buys=10, positive_12m=True), 3),
        # positive_12m False zeroes it out regardless of count
        (InsiderTrackRecord(prior_buys=5, positive_12m=False), 0),
    ],
)
def test_track_record_points(tr: InsiderTrackRecord | None, expected: int) -> None:
    cs = score(7, tr, 0.0, _intensity())
    assert cs.breakdown["insider_track_record"] == expected


# ---------------------------------------------------------------------------
# Valuation discount dimension
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "discount, expected",
    [
        (0.0, 0),  # at median
        (0.09, 0),  # just under 10%
        (0.10, 2),  # exactly 10% below
        (0.24, 2),  # just under 25%
        (0.25, 3),  # exactly 25% below
        (0.50, 3),  # deep discount
    ],
)
def test_valuation_points(discount: float, expected: int) -> None:
    cs = score(7, None, discount, _intensity())
    assert cs.breakdown["valuation_discount"] == expected


# ---------------------------------------------------------------------------
# Cluster intensity dimension
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intensity, expected",
    [
        (_intensity(distinct=1), 0),
        (_intensity(distinct=2), 2),
        (_intensity(distinct=3, csuite=True), 3),
        (_intensity(distinct=4, csuite=True), 3),
        (_intensity(distinct=5, csuite=True), 4),  # 5+ insiders
        (_intensity(distinct=2, ceo=True, cfo=True, csuite=True), 4),  # CEO+CFO both
        (_intensity(distinct=3, csuite=False), 2),  # 3+ but no C-suite → falls to 2
    ],
)
def test_cluster_points(intensity: ClusterIntensity, expected: int) -> None:
    cs = score(7, None, 0.0, intensity)
    assert cs.breakdown["cluster_intensity"] == expected


# ---------------------------------------------------------------------------
# Score → size mapping, with boundary transitions 3/4, 6/7, 9/10
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total, expected_size",
    [
        (0, 0.03),
        (3, 0.03),
        (4, 0.05),  # 3/4 boundary
        (6, 0.05),
        (7, 0.07),  # 6/7 boundary
        (9, 0.07),
        (10, 0.10),  # 9/10 boundary
        (13, 0.10),  # max
    ],
)
def test_size_for_score(total: int, expected_size: float) -> None:
    assert size_for_score(total) == pytest.approx(expected_size)


# ---------------------------------------------------------------------------
# Full-score integration with boundary transitions
# ---------------------------------------------------------------------------


def test_total_and_size_low() -> None:
    # 7→0, None→0, 0%→0, 1 insider→0  => total 0 → 3%
    cs = score(7, None, 0.0, _intensity(distinct=1))
    assert cs.total == 0
    assert cs.indicative_size == pytest.approx(0.03)


def test_total_4_boundary_size_5pct() -> None:
    # 8→2, None→0, 0%→0, 2 insiders→2  => total 4 → 5%
    cs = score(8, None, 0.0, _intensity(distinct=2))
    assert cs.total == 4
    assert cs.indicative_size == pytest.approx(0.05)


def test_total_7_boundary_size_7pct() -> None:
    # 9→3, None→0, 10%→2, 2 insiders→2  => total 7 → 7%
    cs = score(9, None, 0.10, _intensity(distinct=2))
    assert cs.total == 7
    assert cs.indicative_size == pytest.approx(0.07)


def test_total_max_size_10pct() -> None:
    # 9→3, 4+ buys→3, 25%→3, 5 insiders→4  => total 13 → 10%
    tr = InsiderTrackRecord(prior_buys=4, positive_12m=True)
    cs = score(9, tr, 0.25, _intensity(distinct=5, csuite=True, ceo=True, cfo=True))
    assert cs.total == 13
    assert cs.indicative_size == pytest.approx(0.10)


def test_breakdown_keys_present() -> None:
    cs = score(8, None, 0.0, _intensity(distinct=2))
    assert set(cs.breakdown.keys()) == {
        "f_score",
        "insider_track_record",
        "valuation_discount",
        "cluster_intensity",
    }
    assert sum(cs.breakdown.values()) == cs.total
