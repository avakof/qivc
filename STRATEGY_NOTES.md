# QIVC Strategy Notes — Open Questions for v2.1

Signals and edge cases surfaced by live runs that warrant a spec decision before
the next version. These are **research questions, not implemented behaviour.**

---

## OQ-1 — Former-CEO directors and Track B eligibility

**Surfaced by:** the first live market-wide screens (June 2026), ticker **AZO**.

**The signal.** Brian Hannasch — former CEO of Alimentation Couche-Tard, now a
**non-executive director** of AutoZone — made an opportunistic open-market
purchase of **~$493K**, near AZO's 52-week low. The CMP classifier correctly
labelled it **opportunistic** (no routine same-month pattern), and the buy size
clears the **$250K Track B threshold**.

**Why the system rejected it.** Track B (per PROJECT_BRIEF §1.4 / Phase 2) requires
the opportunistic buyer to be a **C-suite officer**. Hannasch files as a
**director** (`is_officer = False`), so Track B does not fire, and a single buyer
cannot satisfy Track A (which needs ≥3 distinct opportunistic insiders in 7 days).
The candidate was rejected at the `insider_conviction` gate — **correctly, per the
current spec.**

**The open question for v2.1.**
> Should a director who is a **former C-suite operator of a comparable company**
> receive C-suite-equivalent Track B treatment?

Arguments **for** widening:
- Academic insider-information literature weights *informedness*; a former public-
  company CEO arguably reads a 10-K as well as a sitting CFO.
- A near-52-week-low, sized, opportunistic buy by such a person is exactly the
  conviction signal the strategy hunts for.

Arguments **against** (keep the spec):
- "Former CEO of a comparable company" is fuzzy and hard to operationalise
  without a curated mapping → backtest/maintenance burden and look-ahead risk.
- Directors trade on board-level information, not operating P&L detail; the
  C-suite restriction is a deliberate quality bar.
- Widening Track B raises false-positive risk; the strategy is intentionally
  conservative.

**Possible v2.1 designs (if we widen):**
1. A new **Track B-prime**: 1 opportunistic director with a buy ≥ a *higher*
   threshold (e.g. $1M) AND a flag that the director held a C-suite role at a
   public company in the last N years (manual/curated `prior_csuite` list).
2. A **conviction-score bump** rather than gate eligibility: keep Track B
   C-suite-only, but award extra `cluster_intensity` points when a
   former-C-suite director buys — surfaces the name without auto-passing it.

**Recommendation:** prefer option 2 (annotate, don't auto-admit) — it preserves
the conservative gate while not discarding the signal. Decision deferred to v2.1;
requires an explicit brief update before implementation.

---

## OQ-2 — `years_of_history` is not measured (data-quality bug, not strategy)

**Surfaced by:** auditing the live classifications — all insiders reported
exactly `3` years of history.

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
