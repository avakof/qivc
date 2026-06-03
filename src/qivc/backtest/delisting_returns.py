"""
Conservative delisting-return logic (Task 14 Phase B3) — PRE-REGISTERED, not tuned.

When a backtest holds a position in a ticker that delists during the hold, we stop
pretending the position vanished and mark its exit conservatively by reason:

  - bankruptcy / liquidation / Chapter 7 or 11  -> exit at -90% (equity ~wiped)
  - acquisition / merger                        -> exit at last available price
  - listing-standard violation / moved to OTC   -> exit at last major-exchange close
                                                   (floor; no further price assumed)
  - reason unknown / ambiguous                  -> exit at -50% (conservative default)

The numeric exit = last_price x multiplier below. acquisition and listing-violation
both resolve to the last observed price (no penalty / floor); bankruptcy and the
unknown default carry the haircut. The DIRECTION — penalising delisted holds — is
the point; the exact levels are frozen, not optimised.
"""

from __future__ import annotations

from typing import Literal

DelistingReason = Literal["bankruptcy", "acquisition", "listing_violation", "unknown"]

# Pre-registered exit multiplier applied to the last observed price.
DELISTING_MULTIPLIER: dict[str, float] = {
    "bankruptcy": 0.10,        # -90%
    "acquisition": 1.00,       # last available price
    "listing_violation": 1.00, # last major-exchange close (floor)
    "unknown": 0.50,           # -50% conservative default
}

_BANKRUPTCY_KW = (
    "chapter 11", "chapter 7", "bankrupt", "liquidat", "insolven", "receivership",
    "winding up", "wind-down", "wind down",
)
_ACQUISITION_KW = (
    "merger", "acquisi", "acquire", "business combination", "tender offer",
    "taken private", "going private", "scheme of arrangement", "plan of arrangement",
    "consummation of the", "completion of the merger",
)
_LISTING_KW = (
    "listing standard", "continued listing", "minimum bid", "market value of listed",
    "stockholders' equity", "stockholders equity requirement", "rule 12d2-2",
    "noncompliance", "non-compliance", "deficien", "failure to satisfy",
    "moved to otc", "quoted on the otc",
)


def classify_reason(text: str | None) -> DelistingReason:
    """
    Best-effort reason from filing text (bankruptcy > acquisition > listing > unknown).
    None / empty / unrecognised -> 'unknown' (which maps to the conservative -50%).
    """
    if not text:
        return "unknown"
    t = text.lower()
    if any(k in t for k in _BANKRUPTCY_KW):
        return "bankruptcy"
    if any(k in t for k in _ACQUISITION_KW):
        return "acquisition"
    if any(k in t for k in _LISTING_KW):
        return "listing_violation"
    return "unknown"


def delisting_exit_price(reason: str, last_price: float) -> float:
    """Conservative exit price for a delisted hold: last_price x pre-registered mult."""
    mult = DELISTING_MULTIPLIER.get(reason, DELISTING_MULTIPLIER["unknown"])
    return last_price * mult
