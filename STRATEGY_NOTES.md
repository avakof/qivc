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

---

## Bulk ↔ live Form 4 cutover (Task 8.1)

The historical insider store (`form4_historical`, populated by
`scripts/bootstrap_bulk_data.py` from the SEC Insider Transactions Data Sets)
covers **2023q1 through the most recently published quarter**, tracked in the
`bulk_load_progress` ledger. The SEC publishes each quarter ~7 days after
quarter-end (observed; we pad to 21 days before expecting one).

**Cutover rule (to be wired into `form4_agent.py` in Task 8.2):** the daily
screen's trailing 14-day window is sourced from `form4_historical` where
available, **falling back to live EDGAR for filings filed after the most recent
bulk quarter's end date**. Records present in both sources are de-duplicated by
`(cik, accession_number)`, with **bulk winning** as the authoritative dataset.

Two known limitations of the bulk source are documented in full in
[BULK_DATA_NOTES.md](BULK_DATA_NOTES.md) and bear on Task 8.2:

1. **Multi-owner fan-out.** The SEC schema has no per-transaction owner key, so
   joint filings cross-join to one record per (transaction × owner). Among
   *P-code* filings this affects ~18% of accessions (not the ~2.2% market-wide
   filing rate) and inflates record counts ~1.5×. CMP clustering counts distinct
   *buyers*, so reading from `form4_historical` should de-duplicate by
   `accession_number` (or collapse affiliated co-filers) to avoid spurious
   Track-A clusters.
2. **Raw price anomalies.** A few rows carry implausible `TRANS_PRICEPERSHARE`
   values (e.g. ~$29M/share) straight from the SEC source. The strict loader
   filter removes only non-positive prices; a value/price sanity bound is a
   candidate for Task 8.2/8.3.

The cutover wiring and cap removal (`_GLOBAL_SCAN_LIMIT`) are **done (Task 8.2)** —
see "Fan-Out Dedup Policy" below. The full-universe comparison (8.3) and its
write-up (8.4) are **not yet implemented.**

---

## Fan-Out Dedup Policy (Task 8.2)

**Decision: de-duplicate by `accession_number` for cluster detection; preserve
the full multi-owner fan-out for the audit / `insider_classifications` path.**

**Why.** The SEC bulk schema has no per-transaction owner key, so a joint Form 4
with N co-filing owners fans out to N records of the same purchase (see
[BULK_DATA_NOTES.md](BULK_DATA_NOTES.md) §5). A single filing is **one event,
not N signals.** Track A requires ≥3 *distinct* opportunistic insiders; counting
co-filers as separate buyers would spuriously trip Track A for any ticker with
institutional co-filing activity. This is **correctness, not style** — among
P-code filings ~18% of accessions are multi-owner (~1.5× record inflation).

**Where it applies.**
- **Cluster detection** (`apply_cluster_detection` → `detect_clusters`): the
  input is first passed through `cluster_detector.dedupe_by_accession`, which
  keeps **one representative record per `accession_number`** — officer preferred,
  then largest `value_usd`. Records with no accession (the live path's
  single-owner records, synthetic single-ticker transactions) pass through
  unchanged and are never collapsed together. Result: a co-filed event
  contributes one buyer; three *distinct* filings still trip Track A.
- **Audit / `insider_classifications` table**: consumes the **full fan-out** —
  `qivc audit` benefits from seeing every listed insider on a filing, with
  per-(ticker, CIK) buy counts and totals. No dedup here.

**Source semantics.** The bulk path (`bulk_loader.read_purchases`) returns the
full fan-out; the live EDGAR path (`_parse_ownership`) already attributes a
filing to a single chosen owner, so it emits one record per filing and is a
no-op under dedup. `accession_number` was added to `InsiderTransaction` to carry
the provenance the dedup keys on.

**Cutover.** `Form4Agent.fetch` reads the bulk store for filings filed on or
before the latest complete bulk quarter (`bulk_loader.bulk_cutover_date`), and
live EDGAR for the delta filed after it. Overlapping `(cik, accession_number)`
records are de-duplicated with **bulk winning**. Because the bulk store is
quarterly, a short trailing daily window is typically served entirely by the
live delta (the current quarter is never in the bulk store until it closes and
publishes ~7 days later); the bulk store's daily-window benefit is realized in
the ~2 weeks after each quarterly publish, while its larger wins are the CMP
3-year history and historical/backtest windows.

---

## Known Bottlenecks

### Per-CIK history fetch — RESOLVED (Task 8.3 part 1)

Task 8.2 rewired `Form4Agent` to use the bulk store, after which
`InsiderHistoryAgent`'s per-CIK live history fetch became the dominant cost
(one throttled EDGAR call per distinct buyer; observed stalling the 8.2
verification run after the Form-4 parse). **Fixed in 8.3 part 1:**
`InsiderHistoryAgent` now reads the 3-year CMP window from `form4_historical`
when the store covers it (local disk read, ~15 ms/CIK; ~98× faster than live),
falling back to live EDGAR only when the store cannot cover the window. See the
"Per-CIK history" note under the cutover/dedup discussion above.

---

## Daily Screen Operational Performance

The full 14-day market-wide live ingest is the remaining long pole and takes
**~40 minutes (best case) to ~3+ hours** depending on rate-limit behaviour:

- Each Form 4's XML is **fetched sequentially from EDGAR** (`filing.obj()`), and
  the SEC limit is ~8 req/s; a 14-day window is **~19,000 filings**.
- This is **structural**, not a bug: a quarterly bulk dataset never holds the
  current (unpublished) quarter, so the trailing daily window is always served
  live. The bulk store accelerates the *history* lookback and historical /
  backtest windows, not the current 14-day ingest.
- The live path is hardened for unattended runs (Task 8.3 part 2): per-filing
  back-off on 403/429, progress logged + checkpointed every 100 filings / 5 min,
  and a recovery file (`data/form4_recovery/`) keyed on already-parsed
  accession numbers so an aborted run **resumes without redoing successful work**.

### Future work — Task 9 (daily-screen latency)

1. **Parallelise the live `obj()` parse** through the rate-limited executor
   (concurrent up to the 8 req/s ceiling) — roughly **~5× speedup**, bounding the
   14-day ingest near the ~40-minute rate-limit floor.
2. **Incremental delta fetch** — persist the last-seen filing cursor and fetch
   only filings *new since the last run*, turning the daily screen into a
   **~2–3 minute** incremental job instead of re-parsing the full trailing
   window each day.

Until Task 9, run the daily screen on a schedule that tolerates the long live
ingest (e.g. an overnight cron), or use a shorter `--lookback-days` for
interactive checks.

### Backtest history-coverage prerequisite (Task 10)

For a backtest at date **D**, the bulk store must extend back to **D − 3 calendar
years** for CMP classification to function — the classifier needs each insider's
trades across the 3 prior calendar years (a 2025 buy is classified against
2022/2023/2024). The 2025 backtest therefore required bootstrapping **2022** into
the store (it had started at 2023q1, leaving every 2025 insider unclassifiable →
0 clusters → 100% cash). Today's *daily* screen has sufficient coverage (2023–2026
covers the 3 prior years for 2026 candidates); any future backtest of a period
before 2023 needs additional historical quarters bootstrapped first (the SEC
Insider Transactions Data Sets reach back to 2006).

---

## The 100-Filing Cap Discovery (Task 8)

**What it was.** From Phase 7 through the first half of this build, the
market-wide Form 4 scan was hard-capped at `_GLOBAL_SCAN_LIMIT = 100` filings
per run (`form4_agent.py`). A 14-day window contains **~19,000** Form 4 filings,
so the cap limited coverage to **~0.5% of the universe** (100 / 19,157 in the
ground-truth run below).

**Why it mattered — silent invalidation.** Every prior daily-screen analysis in
this build ran against **~100 filings, not the universe.** Those runs reported
"0 candidates," but that zero was **uninformative**: it reflected a near-empty
sample, not the real market. The cap failed silently — no error, a normal-looking
report — which is the most dangerous kind of bug for a research tool, because the
output looked authoritative while resting on <1% of the data. Any conclusion
drawn from a pre-Task-8 screen run should be treated as **void**.

**Resolution (Task 8.1 / 8.2 / 8.3).**
- **8.1** built the local bulk store (`form4_historical`) from the SEC Insider
  Transactions Data Sets — 109,261 P-code records, 2023q1–2026q1.
- **8.2** rewired `Form4Agent` to read the window from the bulk store + live
  delta and **removed `_GLOBAL_SCAN_LIMIT` entirely**, with co-filer
  accession-dedup for cluster counting.
- **8.3** moved the CMP history fetch onto the bulk store (~98× faster) and
  hardened the live ingest for full-window runs.

**First ground-truth measurement** (run `4eff2c27`, 2026-06-02, 14-day window,
~58 min):

| Metric | Value |
|---|---|
| Filings examined | **19,157** (vs the old 100) |
| P-code purchases | 975 |
| Unique insiders classified | **515** |
| Opportunistic / routine / unclassified | **5 / 0 / 510** |
| Track A / Track B clusters | 0 / 0 |
| Candidates | **0 — definitive, not a cap artifact** |
| Run status / sanity | completed / all PASS |

The zero is now **real**: the full universe was examined, only 5 insiders were
opportunistic under strict CMP, and none formed a qualifying cluster. Going
forward, daily screens examine the **full Form 4 universe**; the only remaining
limitation is *latency* (the live ingest), tracked as Task 9 above — not
*coverage*.

---

## OQ-3 — Track B size threshold discontinuity

**Surfaced by:** the first full-universe run (`4eff2c27`, 2026-06-02).

Joseph Wm Foran, founder/CEO of **Matador Resources (MTDR)**, made an
opportunistic open-market purchase of **$244,783**. This is exactly the
strategic profile Track B is designed to catch (officer, opportunistic,
founder-level conviction) — but it fell **$217 short** of the hard **$250K**
threshold and was excluded. (HCWB's Hing C Wong, $160K, missed by more.)

The hard threshold creates a **discontinuity**. Cohen-Malloy-Pomorski does not
specify a fixed dollar amount; it specifies that the trade be *meaningful*.
Whether $244,783 is "meaningful" depends on context (insider wealth, market cap,
sector base rates) that the current implementation does not evaluate.

**v2.1 design question — should Track B's size threshold be:**
- **(a)** a hard floor, as today;
- **(b)** a tiered conviction-score adjustment (high size → +conviction; low
  size → lower conviction but still admitted);
- **(c)** a wealth-relative threshold;
- **(d)** a market-cap-tiered threshold (e.g. 0.025 bps of market cap with a
  $100K minimum)?

**No code change.** Document and defer to v2.1; requires an explicit brief
update before any strategy change (cf. OQ-1's bypass-path discussion — both
concern whether a hard rule should admit a borderline-but-informed signal).

---

## OQ-4 — `years_of_history` duration proxy structurally blocked CMP — **RESOLVED**

**Surfaced by:** diagnostics on the first full-universe run (`4eff2c27`,
2026-06-02). Of 498 distinct insiders, only **4** reached `years_of_history >= 3`;
**510 of 515** ticker-CIK rows were "unclassified" — including CIKs with **2,256
prior buys across 4 calendar years** and one with **16 distinct years** of
history. With essentially no insiders classifiable, **Track A clusters could
never form**, so the screen's "0 candidates" was partly an artifact.

**Root cause.** `years_of_history` was a **duration proxy** —
`floor((today - earliest_filed_in_window) / 365.25)` over a rolling
`[today - 3×365.25, cutover]` window. Because the bulk store starts 2023q1 and
today is mid-2026, the window's old edge (~2023-06) sits right at the store's
depth, so the measure only reached 3 if an insider's earliest in-window filing
fell within ~3 days of the edge. It answered the wrong question (elapsed time
since first trade) instead of CMP's actual one (**did they trade across the
prior calendar years?**).

**Fix.** Replaced the proxy with a **distinct prior-calendar-year count**
(`insider_classifier.compute_years_of_history`): the number of distinct calendar
years, strictly before the candidate's year, in which the insider made a P-code
purchase. The classifier computes this directly from the transactions per
candidate; `InsiderHistoryAgent` populates the audit/display field with the same
measure and now reads a **calendar-year-aligned** history window (Jan 1 of
`year − 3`) so the earliest year is fully captured. This is **closer to literal
CMP** ("traded in each of the prior three consecutive years") than the duration
proxy, and removes the structural cap at the store's depth.

**Expected outcome:** the classification rate rises substantially; the
routine/opportunistic split becomes meaningful instead of overwhelmingly
unclassified. Heavy institutional repeat-traders should now resolve to
**routine** (still excluded from opportunistic), while genuine multi-year
opportunistic buyers become visible — making **Track A clusters possible** where
they were previously suppressed.

**Note.** Duration is no longer computed anywhere; `years_of_history` is now a
distinct-year count throughout. The field is retained on `InsiderHistory` for the
audit trail; the classifiability gate is computed per-candidate in `classify`.

### Verification (full-universe re-run, 2026-06-02)

Re-ran the same 14-day screen after the fix (`0259f310`) and compared to the
pre-fix run (`4eff2c27`). Same 19,157 filings / 975 P-txns / 498 distinct CIKs.

| Metric | `4eff2c27` (duration proxy) | `0259f310` (distinct-year) |
|---|---|---|
| Opportunistic (distinct CIK) | **4** | **55** |
| Routine (distinct CIK) | 0 | 11 |
| Unclassified (distinct CIK) | 494 | 432 |
| Classifiable (≥3 yrs) | 4 | **66** |
| Track A clusters | **0** | **1** (UBCP) |
| Track B clusters | **0** | **3** (ENPH, NXDT, PSEC) |
| Tickers passing `insider_conviction` | 0 | **4** |
| Per-gate rejections | insider_conviction 358 | insider_conviction 354, **valuations 4** |
| Candidates | 0 | 0 |
| Runtime | ~58 min | **~24 min** (history now bulk-served) |

The split is now meaningful (routine/opportunistic both populated; heavy repeat
traders correctly resolve to routine). **Clusters now form** where the duration
proxy structurally suppressed them — e.g. Track B on ENPH (CEO Kothandaraman,
$337K), PSEC (Chairman/CEO John Barry, $1.997M), NXDT ($262K), and a Track A
cluster on UBCP (5 opportunistic insiders). All four were then rejected at the
**valuations** gate — but that rejection was **structural, not substantive**: the
valuation data/sector-median was unavailable (see OQ-5), so the gate could not be
evaluated rather than the names being expensive. The universe still yields
**0 candidates**. Run status `completed`; sanity passes (0 candidates → vacuous).
The classifier is now trustworthy for the backtest.

---

## OQ-5 — Sector-medians pipeline is a stub (valuation gate structurally UNVERIFIABLE)

`scripts/refresh_sector_medians.py` is an **empty stub**. The live daily screen
has no point-in-time sector medians, so `ValuationAgent` supplies
`sector_median_metric_value=None` and the valuation gate returns **UNVERIFIABLE**.
Because `valuation` is in `sanity.REQUIRED_GATES` (every survivor must have it
`passed=True`, never None), **UNVERIFIABLE valuation effectively blocks candidacy**
in production.

**Implication.** In run `0259f310` (post-OQ-4), the four clustered names rejected
"at valuation" were rejected **structurally** — the gate could not be evaluated —
not because their valuations were poor. The same is true of `revisions` and
`short_interest`, which also lack a populated data source.

**v2.1 work:** build `refresh_sector_medians.py` — a weekly pipeline computing
industry-median forward P/E (and the other sector-appropriate metrics) across the
~1,900-ticker IWM universe — to make the valuation gate active in production.

**For the 2025 backtest (Task 10):** valuation, revisions, and short_interest run
as **UNVERIFIABLE** (no point-in-time data). To let the strategy trade and be
reviewed, the backtest defines candidacy and sanity against the **5 binding gates
that DO have point-in-time data — regime, insider_conviction, F-Score, GP/A,
liquidity** — and documents the three UNVERIFIABLE gates as a known limitation
(BACKTEST_LIMITATIONS.md). This deviates from production's 8-gate REQUIRED set; it
is a backtest-scoped relaxation, not a strategy change.

---

## OQ-6 — Quality core is incompatible with the small-cap insider-cluster universe

**Surfaced by:** the 2025 full-QIVC backtest (Task 10), which made **0 trades** —
13 names clustered but the quality core rejected all (12 at F-Score, 1 at GP/A).

**Finding.** Among 2025 IWM names with opportunistic insider clusters, **8 of 13
were Financials or REITs**. Piotroski F-Score and Novy-Marx GP/A are built for
**non-financial operating companies**: banks/BDCs have no gross-profit line
(GP/A ≈ 0), and the F-Score gross-margin / asset-turnover / current-ratio
components are ill-defined for financials. Novy-Marx (2013) explicitly excludes
financials from the gross-profitability anomaly. So the gates *correctly* reject
these names — but it means the strategy's quality core and the small-cap
insider-clustering universe **barely intersect**: opportunistic insider buying in
small-caps is disproportionately a financials/REIT phenomenon, exactly where the
quality filter cannot operate.

**v2.1 design question.** Should QIVC:
- **(a)** exclude Financials/REITs from the universe (honest: the quality core
  can't judge them) — narrows the strategy to where it has an edge;
- **(b)** add a **financials-specific quality model** (e.g. ROTCE, efficiency
  ratio, NPL trends for banks; FFO/AFFO coverage and leverage for REITs) so
  insider clusters in those sectors can be evaluated;
- **(c)** accept that the strategy structurally won't trade financials, and
  expect long cash periods.

**No code change.** Documented; requires a brief update before any strategy
change. This is the single most consequential finding of the backtest: the
0-trade result is not a malfunction but a structural mismatch between the signal
universe (insider clusters) and the quality screen (non-financial value).

---

## Proposed QIVC v3.0 — top-N composite scoring (FUTURE WORK, not implemented)

**Motivation.** Both the strict v2.0 and the loosened v2.1 backtests produced
**0 trades in 2025** (100% cash, +4.08%, lagging IWN +12.6%). The cause is not
parameter tuning — it is the **"all gates must pass" framework** combined with a
quality core (F-Score, GP/A) built for profitable non-financial value stocks,
applied to an insider-cluster universe dominated by **financials/REITs** (v2.0)
and, once those are excluded, **unprofitable biotech** (v2.1: 7/11 clustered
names were negative-ROA Health Care). A hard AND of 5–8 gates means one
inapplicable gate (GP/A for a bank, positive-ROA for a clinical-stage biotech)
zeroes the name. **This is a framework problem; v3.0 replaces the boolean gate
chain with a continuous composite score.**

### Framework (a *framework* change, not a parameter change)

- **Universe:** IWM **excluding Financials and Real Estate** (OQ-6 — the quality
  factors are undefined there).
- **Monthly composite score per stock:**
  - **40% insider signal** — CMP opportunistic activity in the last 90 days,
    weighted by recency and conviction (cluster size, C-suite, $ size).
  - **30% quality** — F-Score (normalized 0–1) + GP/A z-score **within sector**.
  - **20% valuation** — composite of sector-relative P/E, P/B, EV/EBITDA
    (requires the OQ-5 sector-median pipeline to exist first).
  - **10% forward momentum** — EPS revisions when available, else 0.
- **Selection:** rank the universe by score, **hold top N** (default **N=10**),
  **equal-weight** initially.
- **Rebalance:** monthly; a stock **stays if still in the top N** — holding period
  **emerges from score persistence** (no fixed duration).
- **Risk controls:** **sector cap 30%**; **regime overlay** — risk-off caps total
  equity exposure at **60%** (rest to T-bills).

### Why this should trade where v2.x can't
Composite scoring **never hard-rejects** on a single inapplicable factor: a
strong insider + valuation name with mediocre quality still ranks; a biotech with
no GP/A simply scores 0 on that sub-component rather than being eliminated. It
will always hold ~N names (subject to the regime cap), so it produces a testable
track record instead of cash.

### Validation protocol (anti-overfitting is mandatory)
- **Cross-validate across regimes:** backtest **2022, 2023, 2024, 2025
  independently** with the *same* parameters. Robustness across regimes matters
  more than any single year (2022 = bear/value, 2023–24 = recovery/growth,
  2025 = growth-led).
- **Constrained search space:** test only **N ∈ {5, 10, 20}** (3 trials) — do NOT
  sweep many N values.
- **Deflated Sharpe Ratio:** apply DSR (Bailey–López de Prado 2014) with the
  trial count = number of configurations tested (now well-defined, unlike the
  single-trial v2.x backtest where DSR was null), to haircut for selection.
- **Report each regime year separately**, with standard errors. Per-name
  qualitative review remains mandatory.
- Data prerequisite: bulk store must extend to **(earliest backtest year − 3)**;
  a 2022 backtest needs 2019+ history bootstrapped.

### Status
**Not implemented.** Estimated build effort **3–5 days** (composite scorer,
sector-relative normalization, the OQ-5 sector-median pipeline as a dependency,
top-N selection engine, 4-year cross-validation harness with DSR). This is a
**framework change** requiring an explicit brief update and a go-ahead decision —
gated on the Step 1 outcome above (0 trades → v3.0 is warranted, not urgent vs.
other work, per the user's call).

### v3.0 implementation notes (Phase 2 dry-run findings)

**Observed concentration characteristic:** universe ∩ opportunistic ∩
quality-evaluable typically yields **1–3 names per month**. The strategy is
**naturally concentrated, not diversified** — position sizing accordingly.
Consequences observed in the Phase 2 dry-run (2025 Jan–Mar, real data):
- The portfolio averaged **1–1.7 names held even at N=10** — the binding
  constraint is **universe size, not N**. The N=5 vs N=10 grid dimension may
  therefore barely differentiate (both hold the same handful of names).
- **Single-name risk per position is high** (few names, large weights).
- The **30% sector cap rarely binds** at this universe scale.
This is a property of the insider-cluster signal in small-caps, not a defect; it
means v3.0 is a concentrated, idiosyncratic-risk book by construction.

## Phase 4A Winner/Loser Descriptive Patterns

> ⚠️ **HYPOTHESES, NOT FEATURES.** The items below are *descriptive observations*
> from a single config (`v3_2025_N10_thr90_hold30`, **22 closed trades**) in one
> survivor-biased year. **No statistical tests were run** (n is far below any
> significance floor), and none should be. Nothing here is validated, nothing is
> implemented, and nothing changes the scorer. Each is listed as a **candidate to
> test if/when proper point-in-time cross-validation exists** (Phase 4B+). Full
> table + caveats: `BACKTEST_ANALYSIS_4A_WINNERS.md`. Recall Task 11:
> composite-vs-return R² = 0.004 — the score did not predict returns.

The descriptive table groups 12 winners vs 10 losers (6 trades still open at
year-end are excluded). What the eye picks out — **to test later, not to act on:**

- **H1 — The composite did not separate outcomes.** Median composite 0.690 (win)
  vs 0.688 (loss). The trade-level echo of R²=0.004. *Test:* does the composite
  rank by realized return in *any* PIT regime year?
- **H2 — Opposite factor mixes, same composite (the most interesting one).**
  Winners reached ~0.69 via **high insider (median 0.954) / low quality (0.528)**;
  losers via **lower insider (0.77) / high quality (0.766)**. The 40/30 blend
  averaged a possible insider-conviction tilt away. *Test:* (a) does an
  insider-conviction-weighted score outperform the blend? (b) Is Piotroski/GP-A
  quality miscalibrated — or even *contrarian* — in a small-cap insider universe?
  *Caveat:* STAA, CDXS, MDGL are high-insider **losers** → weak, noisy.
- **H3 — Beaten-down-2024 winners.** Several winners fell hard in 2024, drew
  insider buying, then rose in 2025 (CVI −37%, JELD −56%, RIG −40%, KALV −30%).
  *Test:* prior-year drawdown + insider buying → mean-reversion bounce? *Caveat:*
  SLSN was +307% in 2024 → −36% loss.
- **H4 — Winners skewed larger-cap** (median ~$940M vs ~$524M). *Test:* a size
  tilt? *Caveat:* MDGL (~$10B) was a loser — fragile.
- **H5 — No visible hold-period or sector separation.** Median hold 60.5d both;
  Energy and Health Care appear in both groups. Probably nothing to test.
- **H6 — Repeat-entry-after-loss (BATRA, TDW).** The strategy has **no outcome
  memory**: it re-buys a name whenever it re-qualifies on fresh insider activity.
  Two names repeated (3 re-entries). After-loss re-entries landed near zero
  (BATRA-May +0.9%, TDW-Jul −0.4%). *Test:* a skip-or-size-down-after-loss rule?
  *Caveat:* **n=2 re-entries proves nothing.**

**How to test these honestly (Phase 4B+ prerequisites):** PIT Russell-2000
membership (kills survivor bias), multi-year cross-section (2019–2025), and a
pre-registered hypothesis list so these are not re-discovered as post-hoc noise.
Until then they are curiosities, not signals.

## Phase 4A — CLOSED (2026-06-02)

Phase 4A (the 2025, 18-config v3.0 grid + analysis) is **closed**. Deliverables:
`BACKTEST_REVIEW_V3_GRID.md` (Part A grid + survivor-bias finding),
`BACKTEST_ANALYSIS_4A.md` (Task 11 deep-dive, R²=0.004),
`BACKTEST_ANALYSIS_4A_WINNERS.md` (this winner/loser description),
`BACKTEST_DATA_PIT_INVESTIGATION.md` (PIT membership data-source options).

### 🚫 DO NOT DEPLOY — research only

**Nothing in Phase 4A is tradeable. There is no demonstrated edge.** Do not put
real capital, or a live/automated order path, behind any v3.0 config — including
the headline `N10_thr90_hold30` (+74.9%). Reasons, all documented above and in
`BACKTEST_LIMITATIONS.md`:

1. **Survivor/membership bias (dominant):** the 2026-06 IWM snapshot was applied
   to 2025. Delisted names are absent; results are conditioned on survival.
2. **The score does not predict returns:** composite-vs-return R² = 0.004
   (Task 11); winners and losers have identical median composites (H1).
3. **The +74.9% is concentration luck, not signal:** the best config made ~65% of
   its return from 3 trades on a 1–8 name book; removing 2025's >+100% monster
   winners did **not** cut the median return (Task 11, Analysis 4).
4. **n is statistically void:** one year, one regime, 8–44 trades/config — below
   any significance floor. Deflated Sharpe < 1.
5. **Some gates ran UNVERIFIABLE** (valuation/momentum neutral; no PIT sector
   medians or EPS-revision snapshots) — see OQ-5 and `BACKTEST_LIMITATIONS.md`.

This system is a **research screen**. Any forward use must be **paper only** until
a PIT, survivor-bias-free, multi-year cross-validation shows out-of-sample edge.

### Forward path: `qivc paper`

The honest next step is **forward, out-of-sample paper trading** — record what the
v3.0 composite would do *going forward* (genuinely unseen data, no survivor bias),
and accumulate a real out-of-sample track record. The CLI command:

```
qivc paper --as-of 2026-06-02 --config N10_thr90_hold30
```

builds today's v3.0 composite portfolio (insider-active universe → score →
construct) and **appends an intended-book snapshot to a paper ledger**
(`data/paper/ledger.jsonl`). It places **no orders** and prints a research-only
banner. It is a data-collection tool for forward validation, not a trading bridge.
See `qivc paper --help`.

### Closure checklist

- [x] 2025 18-config grid run + reproducible (seed=42, byte-identical).
- [x] Red-flag/look-ahead investigation done (bfill bug found + fixed, `8f7081d`).
- [x] Survivor-bias quantified and documented as the dominant confound.
- [x] Task 11 deep-dive (score has no predictive power; concentration luck).
- [x] Winner/loser descriptive analysis → hypotheses logged (this section).
- [x] DO NOT DEPLOY warnings recorded here + `BACKTEST_LIMITATIONS.md`.
- [x] `qivc paper` forward-validation command shipped.
- [ ] **DEFERRED to Phase 4B:** PIT membership source decision (CRSP/WRDS vs
  Norgate), then 2022–2024 cross-validation + Phase 5 DSR-per-config report.

## Task 12 — Technical-Oversold Factor + 5 Pre-Registered Weight Hypotheses

> ⚠️ **EXPLORATORY, IN-SAMPLE, SURVIVOR-BIASED 2025. DO NOT DEPLOY.** The 5
> hypotheses below are pre-registered weightings tested ONCE across the 18 Phase-4A
> configs. None is a validated feature; the output is "which earned a PIT test."
> Full report: `BACKTEST_V3X_TECHNICAL_TEST.md`.

**Phase 1** added a 5th composite factor, **technical-oversold** (Baldwin Filter-4
inspired), with PRE-REGISTERED, untuned parameters: Wilder RSI(14) inverted +
distance below the 60-day high, 0.5/0.5 blend, then percentile-ranked like the
other factors. `src/qivc/backtest/technical.py`; weighted 0.0 in baseline so v3.0
is unchanged. (Do NOT tune RSI period / lookback / blend — tuning them on 2025
would be the data-snooping we're avoiding.)

**Phase 2** tested EXACTLY these 5 weightings (insider/quality/valuation/momentum/
technical), headline metric = Score-Return R² (baseline 0.004):

| Hyp | Weights | Pooled R² | cfgs R²>0.01 | Med Return | Note |
|---|---|---|---|---|---|
| H10 | 40/30/20/10/0 | 0.0040 | 9/18 | 37.5% | baseline control |
| H2  | 70/10/10/10/0 | 0.0351 | 12/18 | 31.8% | down-weight quality, no tech |
| H13 | 40/0/20/10/30 | 0.0415 | 18/18 | 49.6% | technical replaces quality |
| H14 | 50/0/10/10/30 | 0.0452 | 18/18 | 43.9% | full-Baldwin (highest R²) |
| H15 | 35/15/20/10/20 | 0.0164 | 15/18 | 26.5% | technical supplements quality |

**Exploratory findings (NOT validated features — hypotheses for PIT testing):**

- **T1 — The R² lift is real and structural in-sample.** Every quality-down-weighted
  hypothesis beats baseline R²=0.004; the technical-heavy H13/H14 reach ~0.04–0.045
  (~10×) and clear R²>0.01 in **18/18 configs** — not one lucky config. This is the
  first ranking in the project to correlate with forward returns beyond noise.
- **T2 — Most of the gain is from DROPPING quality, not adding technical.** H2
  (quality→10%, no technical) alone lifts R² to 0.035. Piotroski+GP/A quality was
  *diluting* the ranking in this small-cap insider universe (echoes Phase-4A H2:
  quality was higher in losers). Technical adds an increment on top.
- **T3 — Replace beats supplement.** H13 (drop quality) > H15 (keep 15% quality) on
  both R² and return.
- **T4 — H13/H14 raised BOTH return and R²; treat that as ONE effect, not two.** In
  a 2025 small-cap mean-reversion tape an oversold factor both predicts returns and
  rides the bounce — the extra return is the same regime mechanism as the R², not
  independent confirmation. **The dominant risk is that the whole lift is a 2025
  mean-reversion artifact.**
- **T5 — Deflated Sharpe is uninformative here** (all ~0.85–0.99 across 5 hyps);
  T=12 + 90 cells → do not let DSR tip the decision either way.

**Pre-registered next test (if PIT data is acquired):** does the drop-quality +
technical-oversold ranking (H13/H14) still beat baseline R² on survivor-bias-free,
multi-year data — or was it a 2025 mean-reversion artifact? **One clean test, not
more in-sample tuning.** Do NOT fine-tune the winner, do NOT sweep technical
proportions, and do NOT switch `qivc paper` off the v3.0 baseline — paper-trading
continues on the actually-built model to gather honest forward data.

## Task 14 — PRE-REGISTERED criterion for the free SEC-based PIT validation

> ⚠️ **Committed BEFORE any Task-14 data was collected** (this section is the first
> action of Task 14), so the pass/fail bar cannot be moved after seeing results.
> Free-data path: SEC Form 25/15 delisting filings + free Russell 2000 historical
> membership + conservative pre-registered delisting-return rules. Still
> survivor-bias-corrected IN-SAMPLE — **a pass means "worth forward validation,"
> NOT "deploy."** DO NOT DEPLOY regardless of outcome.

**Hypotheses (frozen — only these 3, no new weightings, no tuning):**
- H10 baseline: 40/30/20/10/0 (insider/quality/valuation/momentum/technical)
- H13: 40/0/20/10/30
- H14: 50/0/10/10/30

**Primary test — 2 years (2022 + 2025):** Does H14 (and H13) beat H10's pooled
Score-Return R² in **BOTH** 2022 (trend/down regime) **AND** 2025 (mean-reversion
regime)?
- **FAIL in 2022 → "regime artifact, not durable edge." STOP.** Do not deploy. Do
  not collect 2023/2024. 2022 is the decisive year.
- **PASS both → proceed to the 4-year confirmation.**

**Confirmation test — 4 years (add 2023 + 2024):** Does the R² improvement hold in
**≥3 of 4 years, INCLUDING 2022**?
- **Yes → strongest evidence the project has produced.** Next step is careful
  forward paper-trading — still NOT blind deployment.
- **No → the 2-year pass was partly luck; heavy skepticism.**

**Conservative, pre-registered delisting-return rules (Phase B; not tuned):**
bankruptcy/liquidation/Ch.7/11 → −90%; acquisition/merger → last available price;
listing-standard violation / moved-to-OTC → last major-exchange close (floor,
no further price); unknown/ambiguous → −50% (conservative-uncertain). The direction
is the point: stop pretending delisted holds vanished, and penalize them.
