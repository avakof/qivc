"""
Backtest universe loader.

The investable universe for the 2025 backtest is a **committed, dated snapshot**
of the iShares Russell 2000 ETF (IWM) holdings. It is stored in the package
(not the gitignored runtime ``data/`` dir) so the backtest is reproducible.

IMPORTANT — survivor & membership bias: this is the IWM constituent set as of
the snapshot date (``UNIVERSE_AS_OF``), applied to a 2025 backtest. Names that
were in the Russell 2000 during 2025 but have since been delisted, acquired, or
dropped from the index are **absent**; names added after 2025 are **wrongly
included**. Both bias results upward (we only screen survivors that are
currently small-cap index members). This is documented in BACKTEST_LIMITATIONS.md
and surfaced in the backtest review.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

# Snapshot provenance — iShares Russell 2000 ETF "Fund Holdings as of" date.
UNIVERSE_AS_OF = "2026-06-01"
_SNAPSHOT = Path(__file__).parent / f"iwm_constituents_{UNIVERSE_AS_OF}.csv"


@dataclass(frozen=True)
class Constituent:
    ticker: str
    name: str
    sector: str  # iShares GICS sector label


def load_iwm_constituents(path: Path | None = None) -> list[Constituent]:
    """Load the committed IWM constituent snapshot (equities only)."""
    src = path or _SNAPSHOT
    with src.open(newline="", encoding="utf-8") as fh:
        return [
            Constituent(
                ticker=row["ticker"].strip(),
                name=row["name"].strip(),
                sector=row["sector"].strip(),
            )
            for row in csv.DictReader(fh)
            if row.get("ticker", "").strip()
        ]


def iwm_tickers(path: Path | None = None) -> set[str]:
    """The set of IWM constituent tickers — used to restrict the screen universe."""
    return {c.ticker for c in load_iwm_constituents(path)}


def iwm_sector_map(path: Path | None = None) -> dict[str, str]:
    """ticker -> iShares GICS sector (used for the sector-cap overlay in backtest)."""
    return {c.ticker: c.sector for c in load_iwm_constituents(path)}
