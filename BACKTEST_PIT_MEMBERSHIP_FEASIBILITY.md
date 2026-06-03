# Free PIT Russell-2000 Membership — Feasibility Gate (Task 14 Phase A)

**Investigation only. No data collected for the backtest yet, no code built.**
Question: can we obtain clean-enough point-in-time Russell-2000 membership for
**mid-2022 and mid-2025** (and 2021/2023/2024 as margin) from **free** sources, on
a Mac, no VM, no subscription — to remove the survivor bias that made Phase 4A and
Task 12 in-sample-only?

## VERDICT: ✅ PASS — proceed to Phase B

A complete, free, reproducible PIT-membership pipeline exists:
**iShares IWM N-PORT filings on SEC EDGAR → OpenFIGI identifier mapping → ticker
set per date.** All findings below are verified against live data (June 2026).

---

## The winning source: iShares IWM N-PORT-P on SEC EDGAR

IWM (iShares Russell 2000 ETF) is a series of **iSHARES TRUST, CIK 0001100663**,
which files **NPORT-P** (full portfolio holdings) with the SEC. The public N-PORT
for each fund's quarter-end is on EDGAR for free — **same infrastructure and
bulk-archive pattern as our existing Form 4 loader.** Confirmed IWM filings, all
dated to the **June 30 reconstitution period** (when Russell sets annual
membership):

| Period | Accession | Holdings |
|---|---|---|
| 2020-06-30 | 0001752724-20-176935 | (margin) |
| 2021-06-30 | 0001752724-21-186233 | (margin) |
| **2022-06-30** | **0001752724-22-193728** | **1,996** |
| 2023-06-30 | 0001752724-23-191317 | ✓ |
| 2024-06-30 | 0001752724-24-194120 | ✓ |
| **2025-06-30** | **0001752724-25-210405** | **2,123** |

- Each holding gives `<name>`, `<lei>`, `<cusip>`, `<isin>`, balance, value,
  `assetCat`, `payoffProfile`. The XML is `primary_doc.xml` in each accession
  (~1.8–3.3 MB), parseable with the same discipline as the Form 4 archives.
- **Timing is right and not look-ahead for membership:** Russell reconstitutes at
  end of June and the composition is publicly announced then; using the 06-30
  N-PORT as that year's membership universe is the standard, defensible PIT
  definition. (The filing is public ~60 days later, but index *membership* is known
  at reconstitution — no look-ahead on which names are in the index.)
- **Apples-to-apples with Phase 4A:** Phase 4A used a *current* IWM-holdings
  snapshot applied to all years (the survivor bias). This swaps in the
  **as-of-date** IWM holdings — same universe definition, now point-in-time. The
  ETF ≈ the index (tiny cash/sampling differences), more than clean enough for a
  regime-signal R² test.

### The one wrinkle: N-PORT has no ticker — solved free by OpenFIGI

N-PORT identifies holdings by **CUSIP / ISIN / LEI / name**, not ticker. Our
pipeline (insider data, prices) is ticker-keyed, so we must map. CUSIP→ticker is
normally paywalled (CUSIP Global Services), **but OpenFIGI (Bloomberg's free FIGI
service) maps CUSIP/ISIN → ticker at no cost and retains delisted securities.**
Verified live:

- CUSIP `29280W109` → **NRGV** (Energy Vault); batch of 10 spread across the 2022
  book returned **8/8 tickers for every holding with a real CUSIP**.
- Identifier coverage is near-total: **2022 = 95.7% valid CUSIP** (85 of the
  remaining 86 have an ISIN fallback) → ~99.95% mappable; **2025 = 94.3% valid
  CUSIP** (119 of 120 have ISIN) → ~99.95% mappable. Effectively 1 unmapped name
  per year — well within "small gaps OK."
- OpenFIGI is free and rate-limited (anonymous ~hundreds of jobs/min; an optional
  free key raises it). A one-time pull of ~4k identifiers (2 years × ~2k) takes a
  few minutes and is **cached to disk** for byte-identical re-runs.
- **Fallback** if OpenFIGI ever fails for a name: normalized name-match against the
  free SEC `company_tickers.json` (covers active names) + Form 25 ticker (covers
  delisted names, Phase B). So no single point of failure.

### Delisted names (the actual survivor-bias fix)

Delisted/acquired 2022 names (Tupperware, Cano Health, KAR, Sovos, Masonite,
Callon…) are **absent from the current `company_tickers.json`** — which is exactly
why a name-match alone scored only ~68%. They are recovered two ways, both free:
(1) **OpenFIGI retains delisted FIGIs/tickers**; (2) **SEC Form 25/15 delisting
filings** (Phase B) carry the delisted ticker + CIK + date + reason. These names
are the point of the exercise — present in the 2022 N-PORT, gone from today's
snapshot.

---

## Other candidates checked (and why N-PORT wins)

- **FTSE Russell reconstitution recaps** (ftserussell.com): publish adds/deletes
  and weights for recent reviews only, as PR/PDF, license-encumbered — **no
  free machine-readable full baseline list** back to 2022. Reconstructing PIT
  membership from adds/deletes PDFs is error-prone. Inadequate. (Consistent with
  the earlier `BACKTEST_DATA_PIT_INVESTIGATION.md` finding.)
- **iShares.com historical holdings**: the site exposes a **current** holdings CSV
  (with tickers) but does **not** publish dated historical holdings. N-PORT is the
  free historical substitute.
- **GitHub / datahub historical-constituent repos**: snapshots, unmaintained, not
  true PIT entry/exit histories, unverified — not trustworthy for a dated backtest.
- **SEC N-PORT from IWM's issuer**: ✅ the source — dated, complete (~2k holdings),
  free, official, every year we need.

---

## What Phase B will build (only now that the gate passed)

1. **N-PORT membership loader**: pull IWM `primary_doc.xml` for 2022/2025 (+margin),
   parse holdings, map CUSIP/ISIN→ticker via OpenFIGI (cached), store a dated
   membership table. → `get_constituents(index, as_of)`.
2. **SEC Form 25/15 delisting loader** (`delistings_historical`): same EDGAR
   bulk pattern as Form 4, 2021-Q1…2025-Q4; ticker/CIK/date/reason; spot-check
   Revlon (REV, June 2022) etc.
3. **Conservative pre-registered delisting-return rules** (see STRATEGY_NOTES.md
   Task 14): bankruptcy −90%, acquisition last price, listing-violation/OTC last
   major-exchange close, unknown −50%.
4. **`PitUniverseProvider` protocol** (Task 13 was never built) + **`SecPitProvider`**
   implementing it, keeping a `BiasedSnapshot` provider for comparison/regression.

**Cost: $0. Mac-only. No VM, no subscription.** Reproducible via cached EDGAR
pulls + cached OpenFIGI map.

## Honest caveats (carried into the test)

- IWM-ETF-holdings ≈ Russell-2000-index membership, not identical (cash, sampling,
  timing). Fine for a regime-signal test; noted.
- ~1 unmapped holding/year and any OpenFIGI name gaps are dropped — small,
  acceptable, and **logged** (no silent truncation).
- This corrects survivor bias but the test remains **in-sample bias-corrected**, on
  ~2 years. A pass means "worth forward validation," **not deploy** (per the
  pre-registered criterion). DO NOT DEPLOY regardless of result.
