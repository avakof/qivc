"""
Insider cluster detection — pure function.

Track A: ≥3 distinct opportunistic open-market buyers in the same ticker
         within a rolling 7-day window.
Track B: 1 C-suite (officer) opportunistic buyer with value_usd ≥ threshold
         in the same ticker.

No I/O; no imports from qivc.data.
"""

from __future__ import annotations

from datetime import date, timedelta

from qivc.schemas import Cluster, InsiderTransaction

_OPPORTUNISTIC = "opportunistic"
_TRACK_B_MIN_USD: float = 250_000.0  # default C-suite size threshold


def detect_clusters(
    transactions: list[InsiderTransaction],
    classifications: dict[str, str],  # cik → "opportunistic"|"routine"|"unclassified"
    window_days: int = 7,
    track_b_min_usd: float = _TRACK_B_MIN_USD,
) -> list[Cluster]:
    """
    Detect Track A and Track B insider clusters.

    Parameters
    ----------
    transactions:
        All P-code transactions to scan (may span multiple tickers).
    classifications:
        Mapping from CIK to CMP classification string.
    window_days:
        Rolling window size for Track A (default 7 calendar days).
    track_b_min_usd:
        Minimum value_usd for a Track B C-suite buy (default $250 000).

    Returns
    -------
    Deduplicated list of Cluster objects (one per detected signal per ticker).
    """
    # Group by ticker
    by_ticker: dict[str, list[InsiderTransaction]] = {}
    for t in transactions:
        by_ticker.setdefault(t.ticker, []).append(t)

    clusters: list[Cluster] = []

    for ticker, ticker_txns in by_ticker.items():
        sorted_txns = sorted(ticker_txns, key=lambda t: t.transaction_date)

        # ------------------------------------------------------------------
        # Track A — sliding window of ≥3 distinct opportunistic buyers
        # ------------------------------------------------------------------
        opp_txns = [t for t in sorted_txns if classifications.get(t.cik) == _OPPORTUNISTIC]

        found_a = False
        for i, anchor in enumerate(opp_txns):
            cutoff: date = anchor.transaction_date + timedelta(days=window_days)
            window = [t for t in opp_txns[i:] if t.transaction_date <= cutoff]
            distinct_ciks = {t.cik for t in window}

            if len(distinct_ciks) >= 3:
                clusters.append(
                    Cluster(
                        ticker=ticker,
                        transactions=window,
                        track="A",
                        window_start=min(t.transaction_date for t in window),
                        window_end=max(t.transaction_date for t in window),
                        total_value_usd=sum(t.value_usd for t in window),
                    )
                )
                found_a = True
                break  # one Track A cluster per ticker is enough

        # ------------------------------------------------------------------
        # Track B — single C-suite opportunistic buy ≥ threshold
        # ------------------------------------------------------------------
        if not found_a:
            for t in sorted_txns:
                if (
                    classifications.get(t.cik) == _OPPORTUNISTIC
                    and t.is_officer
                    and t.value_usd >= track_b_min_usd
                ):
                    clusters.append(
                        Cluster(
                            ticker=ticker,
                            transactions=[t],
                            track="B",
                            window_start=t.transaction_date,
                            window_end=t.transaction_date,
                            total_value_usd=t.value_usd,
                        )
                    )
                    break  # one Track B per ticker

    return clusters
