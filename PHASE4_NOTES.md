# Phase 4 Notes — Scoring & Risk Overlay

## Acceptance gate results

| Gate | Result |
|---|---|
| `uv run pytest tests/unit/synthesis -q` passes | **PASS** (47 tests) |
| 100% coverage on conviction.py and risk_overlay.py | **PASS** (80/80 statements) |
| Integration test produces conviction scores in final state | **PASS** (asserted in `test_end_to_end_good_ticker_is_candidate`) |

## Resolved contradiction: regime-cap multipliers

The Phase 4 prompt contained an internal contradiction:

- **item 2** (function spec): risk-mid → "cap at **60%**", risk-off → "cap at **30%**"
- **item 4** (test spec): risk-mid "**halves**" (50%), risk-off "**thirds**" (33%)
- **PROJECT_BRIEF §5** (acceptance): risk-mid "**halved**" (50%), risk-off "**third'd**" (33%)

Two of three sources (the test spec and the brief's own acceptance criteria)
agree on halve/third. The user confirmed **50% / 33%**. `apply_regime_cap` uses:

```
risk-on  → ×1.0
risk-mid → ×0.5      (halved)
risk-off → ×0.3333…  (third'd)
```

## Resolved ambiguity: size units vs. cap units

Score→size tiers are written as "3.0% … 10.0%" but the cap signatures default to
`sector_cap=0.20` / `subsector_cap=0.12`, and the cap tests use "22% → flag",
"14% → flag". For all three to be consistent, indicative sizes are stored as
**fractions** (0.03, 0.05, 0.07, 0.10), so a sector total of 0.21–0.22 trips the
0.20 cap and a sub-sector total of 0.14 trips the 0.12 cap. `Candidate.indicative_size_pct`
holds the fraction (documented in the schema).

## Insider track-record dimension

The conviction scorer accepts an `InsiderTrackRecord | None`. The live pipeline
does not yet compute prior-buy track records (that needs a 5-year per-insider
return study), so `apply_synthesis` passes `None` → 0 points for that dimension,
consistent with the brief's "UNCLASSIFIED fallback when history is sparse"
(§13). The scorer and its tests fully cover the 0/2/3-point cases for when this
is wired in.

## apply_synthesis flow (Phase 3 node, now populated)

1. For each surviving candidate: derive F-Score (from the recorded `f_score`
   FilterResult), cluster intensity (distinct insiders, C-suite/CEO/CFO from
   titles), and valuation discount (fraction below sector median).
2. `conviction.score(...)` → total + per-dimension breakdown + indicative size.
3. `apply_regime_cap` scales the size and appends a note to `flags`.
4. `apply_sector_cap` then `apply_subsector_cap` flag overflow candidates.
5. The scored list **overwrites** `state.candidates` (the field's merge reducer
   was removed in this phase — it has only sequential writers, so re-emitting
   must replace, not append).
