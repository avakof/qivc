# QIVC Strategy Notes — Open Questions for v2.1

Signals and edge cases surfaced by live runs that warrant a spec decision before
the next version. These are **research questions, not implemented behaviour.**

---

## OQ-1 — First-time-buyer signals vs. CMP classifiability (reframed)

**Surfaced by:** the first live market-wide screens (June 2026), ticker **AZO**.

**The signal.** Brian Hannasch — former CEO of Alimentation Couche-Tard, an
AutoZone **director** since Feb 2022 — made an open-market purchase of **~$493K**,
near AZO's 52-week low. On its face this is exactly the conviction signal the
strategy hunts for: a senior, informed insider buying size into weakness.

**Why the system rejected it (corrected after the OQ-2 fix).** The *original*
reading was "Track B requires a C-suite officer and Hannasch is a director." That
was wrong about the binding constraint. With `years_of_history` now measured from
actual trading history (OQ-2), Hannasch has **only one open-market purchase ever
at AZO** → `years_of_history = 0` → **UNCLASSIFIED** under strict CMP. He is
excluded from the opportunistic count *before* Track A/B eligibility is even
considered. The candidate fails at the `insider_conviction` gate because he is
**unclassifiable**, not because of his officer/director status.

This matches Cohen-Malloy-Pomorski (2012): an insider is classifiable only if
they **traded at least once in each of the prior three years**. CMP measures
*trading-pattern history*, not employment tenure or seniority. Board tenure is
irrelevant; a one-buy history is unclassifiable by construction.

**The reframed open question for v2.1.**
> CMP's exclusion of first-time buyers is about **classifiability, not about lack
> of information**. A former public-company CEO buying $493K into a 52-week low is
> plausibly *more* informed than a routine repeat-trader — yet CMP discards the
> signal purely because there's no multi-year trading pattern to classify.
>
> Should **single-buy events from first-time-buyer insiders** trigger a *separate
> signal path that bypasses CMP* (e.g. gated on seniority + size + proximity to
> lows), rather than being silently dropped?

This is **not** the earlier "widen Track B to former-CEO directors" question —
that framing mis-located the failure. The real design tension is whether
classifiability should be a hard prerequisite for *any* insider signal, or only
for the routine/opportunistic *distinction*.

**Constraints / risks (unchanged):**
- A bypass path is harder to backtest (no historical pattern to lean on) and
  raises false-positive risk; the strategy is intentionally conservative.
- "Senior + informed" is fuzzy to operationalise without curated data.

**Decision deferred to v2.1.** Document only; **do not** implement a bypass path.
Requires an explicit brief update before any strategy change.

---

## OQ-2 — `years_of_history` was not measured (data-quality bug) — **RESOLVED**

**Surfaced by:** auditing the live classifications — all insiders reported
exactly `3` years of history. **Fixed**; see the resolution section below.

`InsiderHistoryAgent` computes `years_of_history` as
`min(years, max(1, today.year - (today.year - years)))`, which is algebraically
**always `years`** (the lookback parameter, default 3). It is **not measured from
the fetched filings.** Consequences:
- The classifier's safety fallback (`years_of_history < 3 → unclassified`) is
  effectively **dead** in the live pipeline — it never triggers.
- The routine/opportunistic decision relies entirely on whether the fetched
  transactions show the 3-prior-consecutive-year same-month pattern; an insider
  with genuinely sparse history is silently treated as having full history.

**Fix (recommended for v2.1, or sooner):** derive history depth from the data —
e.g. `years_of_history = floor((as_of - earliest_filed_date).days / 365)` or the
count of distinct prior calendar years with filings — so the `unclassified`
fallback works as the brief intends (§5 Phase 2 edge case).

**Caching gap (related):** PROJECT_BRIEF §3.2 specifies insider classifications
should be cached (TTL 90 days). They are **not** cached — every run re-fetches
each CIK's history live from EDGAR (~1 throttled request per unique CIK). Fine
for the bounded daily scan; costly for a full-universe scan. Caching is a v2.1
item.

---

## OQ-2 Resolution: `years_of_history` Semantics

The field measures **trading-history depth, not employment tenure.**
`InsiderHistoryAgent` now computes
`years_of_history = floor((today - earliest_P_code_filing) / 365.25)`, or `0`
when the insider has no prior open-market purchases in the fetched window.

This matches Cohen-Malloy-Pomorski (2012)'s strict definition: an insider is
classifiable only if they **traded at least once in each of the prior three
years.** Tenure is irrelevant; trading-pattern history is what matters.

**Implications:**
- First-time buyers (no prior open-market activity at this ticker) →
  `years_of_history = 0` → **unclassified** → excluded from the opportunistic
  count **regardless of seniority** (this is the Hannasch/AZO case — see OQ-1).
- This is correct per CMP but **more restrictive than common retail
  interpretations** of "insider buying signal."
- Expect **most single-buy events to fail CMP classification** under strict
  semantics; the opportunistic pool is dominated by **repeat-trading insiders.**

**Implementation note:** `years_of_history` measures earliest **P-code (purchase)**
filing only, not earliest open-market (P + S) filing. This is more conservative
than strict CMP, which uses **both P and S** transactions in classification. For
QIVC (long-only, purchase-signal-driven), counting only P is appropriate but
deviates from CMP literally. An insider who sells regularly but buys rarely will
be unclassifiable under our implementation even if CMP would classify them.

**Live verification (post-fix), corrected expectation.** We no longer expect
"Hannasch shows 4y." The observed and *correct* result on a bounded daily scan
is that all surviving tickers' lone P-code buyers show **`0y` / `unclassified`**,
because each was a single purchase with no prior trading history at that ticker,
and CMP correctly excludes them. `years_of_history` now varies across insiders in
proportion to their actual repeat-purchase history (0 for first-time buyers, ≥3
for multi-year repeat traders), rather than the old constant `3`.

**Scope note.** `years_of_history` is bounded above by the history fetch window
(`cmp_history_years`, default 3 years) and is derived from **P-code purchases
only** — so it reflects *purchase* history within that window, not full Form 4
filing tenure. Measuring true multi-year tenure (e.g. for a >3-year routine
check) would require widening `EdgarClient.get_insider_history` beyond 3 years
and scanning all transaction codes; deferred as future work, not required for
correct CMP classifiability.

---

## Sanity Test Coverage Limitations

Invariant 2 (no negative earnings in candidates) is implemented
by checking the F-Score gate's own reason string for
"roa_positive" as a failing component. This catches the failure
mode "F-Score gate correctly identified a negative-ROA name but
a downstream layer let it through" but NOT "F-Score gate
incorrectly evaluated ROA as positive when it was actually
negative."

The latter (F-Score gate wiring bug) would be caught by:
- Unit tests on the F-Score filter with known fundamentals
- A future schema enhancement persisting net_income on the
  per-run fundamentals snapshot

For v2.1, consider persisting net_income, gross_profit, and
total_assets per ticker per run, enabling sanity invariants to
verify gate inputs independently of gate outputs.

Live verification note: the qivc sanity CLI was first run
against run 5de53ecf, which had zero candidates. With zero
candidates, all five invariants pass vacuously (no candidates
means no invariant body executes against real data). The
seeded-violation tests in tests/sanity/ are the actual proof
that each checker fires on its target failure. The first
non-vacuous live exercise of the sanity layer will happen
when a run produces at least one candidate.

Edge case: a sanity check against a nonexistent run_id (typo,
garbage input, empty string) returns "all invariants pass"
because there's no data to violate them. This is defensible —
don't false-positive on typos — but means CI pipelines fetching
run_ids from elsewhere should validate the ID exists before
relying on a passing sanity check as confirmation that real
data was checked.

---

## Design Decision: Short-Circuit vs. Evaluate-All

Current behavior: the gate pipeline is **hybrid**, not a pure short-circuit.
Stage 1 (`regime` → `insider_conviction` → data-availability) short-circuits on
the first failing gate — a ticker that fails one of these never has the later
Stage-1 gates evaluated (this is why a ticker rejected at `insider_conviction`,
e.g. GPUS, records only the gates that actually ran). Stage 2 (the six
quality/valuation gates: `f_score`, `gp_a`, `valuation`, `revisions`,
`short_interest`, `liquidity`) is evaluate-all: all six are computed and every
result is recorded, and only the *first* failure is used as the rejection
reason. So short-circuiting applies through `insider_conviction`, not across the
Stage-2 quality block.

Pros:
 - Faster (saves N gate evaluations per rejected ticker)
 - Cleaner audit log (only logs gates that actually ran)

Cons:
 - Rejection reasons in dossier may be incomplete (a ticker fails
   for one reason, but might have failed for several)
 - Can't compute "which gate kills the most candidates" analytics
   for strategy tuning
 - Sanity tests need to handle the "this gate wasn't evaluated"
   case explicitly

Decision: keep short-circuit for daily live screens (current
default). For backtesting (Phase 7) and strategy analytics, add
a `--evaluate-all-gates` flag to qivc screen that records every
gate result regardless of earlier failures.

`--evaluate-all-gates` is **future work — not yet implemented**; it is the next
step for strategy analytics (it would also let Stage 1 record all gate outcomes
per ticker, removing the "this gate wasn't evaluated" caveat above).
