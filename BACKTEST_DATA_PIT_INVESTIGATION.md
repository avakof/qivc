# Point-in-Time Russell 2000 Membership — Data-Source Investigation

**Investigation only — no purchases, no downloads, no integration code.** Goal:
source per-rebalance-date Russell 2000 / IWM constituent membership for **2019–2025**
*plus* price history for **delisted** names, to remove the survivor/membership bias
that made the Phase 4A grid non-credible (median +37.75%, an artifact of applying
the 2026-06 IWM snapshot to 2025). Findings reflect free public docs/pricing as of
**June 2026**; anything behind a paywall or login is marked unverified.

## Requirements recap
1. Per-quarter (or finer) **Russell 2000 constituent lists, 2019Q1–2025Q4**.
2. **Entry/exit dates** per constituent (PIT membership true/false on any date).
3. **Delisted price history** through the delisting date (capture "held → bankrupt → −97%").
4. Machine-readable + **reproducible** (committed snapshot or re-runnable pull).
5. Map to our existing data (SEC CIK / ticker from `form4_historical`).

---

## Options evaluated

### ❌ Free official: FTSE Russell / LSEG
- Publishes constituents-&-weights spreadsheets, but only **recent** reviews
  (~2-month lag) and **use requires a FTSE Russell licence**. Full **historical**
  reconstitution (2019–2025 PIT membership) is **not** freely machine-readable;
  the annual recon materials are PR/PDF (often additions/deletions, not full PIT
  lists), and there is **no delisted price history**. Reconstructing PIT membership
  from PDFs is error-prone. **Inadequate** for our needs.
- Free third-party scrapes (e.g. a `ikoniaris/Russell2000` GitHub CSV, SureDividend's
  current list) are **snapshots, not PIT entry/exit histories**, unmaintained, and
  unverified — **not trustworthy** for a 2019–2025 backtest.

### ❌ EODHD (eodhd.com) — delisted prices yes, Russell membership no
- Has a **delisted-companies API** (EOD prices for delisted names) ✓ and an
  "Indices Historical Constituents" API — but historical-constituent coverage is the
  **S&P / Dow Jones family (S&P 500/400/600/100)**; **Russell 2000 historical
  membership is not offered** (only a current-components lookup for ~100 indices).
  Pricing $19.99–€99.99/mo. **Useful only as a delisted-price supplement; cannot
  supply PIT Russell 2000 membership.** (One could substitute **S&P SmallCap 600**
  historical membership as a *different* small-cap universe — a strategy change, not
  a fix.)

### ❌ Polygon.io — delisted prices spotty, no membership
- Delisted tickers via `active=false`, but coverage is **"spotty at best"** (often
  ticker only, no dates). **No historical index constituents** (open feature request
  for Russell 2000 membership; not provided). **Inadequate** for both needs.

### ✅ Norgate Data — the retail option that meets all requirements
- **Platinum** US Stocks (or Diamond) includes **historical index constituents**
  (Russell 2000 / Russell 3000 / S&P / Nasdaq) as a **point-in-time true/false
  membership function**, **delisted securities** (~25,222 since 1950; US data to
  1990), and OTC/formerly-listed names — i.e. survivor-bias-free by construction,
  with **delisted price history**.
- **Python integration**: the `norgatedata` PyPI package reads a **local** database
  maintained by the **Norgate Data Updater (NDU)** desktop app. Plugins exist for
  Zipline/Amibroker/RealTest/Wealth-Lab. The `norgatedata.index_constituent_timeseries(...)`
  call yields a daily membership series we can join to our rebalance dates.
- **Cost:** Platinum US Stocks **USD 346.50 / 6 mo** or **USD 630 / 12 mo (~$52.50/mo)**;
  no monthly term. 14-day free trial available (no auto-charge per their site —
  would let us verify Russell coverage before paying).
- **⚠️ macOS friction:** **NDU is Windows-only**; on our darwin box it needs
  Parallels/VMware/VirtualBox. The data lives in a local NDU DB, so a run needs NDU
  installed+updated in a Windows VM (one-off setup; then export a committed snapshot
  for reproducibility).

### ✅ CRSP via WRDS — gold standard, free *if* the institution has access
- **The academic standard** for survivor-bias-free US equities: 20,000+ active **and
  inactive** stocks since 1925/1962, with **delisting codes + delisting returns**
  (the rigorous way to book a bankruptcy loss). Russell membership is available in
  WRDS (Russell module / CRSP-Compustat-Russell linking; exact availability is
  **subscription-dependent**).
- **Cost: $0** if **albertschool.com** has a **WRDS subscription incl. the Russell
  module** — accessed via the `wrds` Python package or web query; we'd commit the
  pulled snapshot for reproducibility. **Biggest unknown:** whether the institution
  actually has WRDS + Russell membership data (varies by school).

---

## Side-by-side (the two viable options)

| | **Norgate Data (Platinum)** | **CRSP via WRDS** |
|---|---|---|
| PIT Russell 2000 membership 2019–25 | ✅ daily true/false | ✅ (subscription-dependent) |
| Entry/exit dates | ✅ | ✅ |
| Delisted price history | ✅ (~25k since 1950) | ✅ + **delisting returns** (best) |
| Money | **~$630/yr** (or $346.50/6mo); 14-day trial | **$0** *if* institution has it |
| Reproducible | ✅ commit snapshot from local DB | ✅ commit pulled snapshot |
| Integration effort | ~2–3 d + **Windows-VM setup** (NDU) | ~2–3 d + WRDS access setup |
| Map to our CIK/ticker | by ticker (needs ticker↔CIK reconcile) | PERMNO↔CIK via CRSP link or ticker |
| Risk of surprise | low (mature retail product); macOS/NDU friction | access/module availability is the risk |
| Quality | excellent for retail | **gold standard** |

---

## Recommendation

1. **First, confirm CRSP/WRDS access** through Albert School (≈1 hr: check for a
   WRDS account + whether the Russell membership dataset is in the subscription).
   If available, **use CRSP/WRDS** — it is free, the academic gold standard, gives
   proper **delisting returns**, and is fully reproducible via a committed snapshot.
2. **If WRDS is not available, subscribe to Norgate Data Platinum** (~$630/yr; start
   with the **14-day free trial** to verify Russell 2000 historical coverage *before*
   paying). It meets every requirement at retail cost; the only real friction is
   running NDU in a **Windows VM** on this Mac.
3. **Do NOT** rely on EODHD/Polygon/free-scrape for membership — none provide
   trustworthy PIT Russell 2000 constituents; at most they're delisted-price
   supplements (and Norgate/CRSP already cover delisted prices).

Prefer free (CRSP/WRDS) if accessible; otherwise Norgate is the minimum-cost option
that fully meets the requirements.

## Edge cases / unknowns (cannot resolve without access)
- **Does albertschool.com have WRDS + the Russell membership module?** Determines
  free-vs-paid. *Needs your input / an access check.*
- **Norgate Russell 2000 coverage 2019–2025** — strong per docs but I can't verify
  the exact constituent counts without the trial.
- **macOS/NDU** — needs a Windows VM (Parallels/VMware); one-off but real on this box.
- **Ticker↔CIK mapping for delisted names** — historical ticker reuse/changes make
  the join to our CIK-keyed `form4_historical` non-trivial; CRSP's PERMNO + the
  CRSP-Compustat link is cleaner than Norgate's ticker-only join.
- **Russell 2000 (index) vs IWM (ETF)** — both Norgate and CRSP give the **index**
  membership, which is *better* than the ETF-holdings snapshot we used (the index is
  the intended universe).

## Time + money for the recommended path
- **Access check (CRSP/WRDS):** ~1 hr, $0.
- **If WRDS:** integration ~**2–3 days**, **$0** — new DuckDB `index_membership` table
  + delisted-price provider; pull once, commit snapshot.
- **If Norgate:** ~**2–3 days** integration + ~**0.5 day** Windows-VM/NDU setup;
  **$346.50 (6 mo) – $630 (12 mo)**; 14-day free trial first to validate coverage.

**STOP — awaiting your direction on which source to commit to before any
integration work. No purchases or paid downloads will happen without your explicit
go-ahead.**

Sources: [Norgate data content](https://norgatedata.com/data-content-tables.php) ·
[Norgate prices](https://norgatedata.com/prices.php) ·
[Norgate NDU FAQ (Windows-only)](https://norgatedata.com/ndu-faq.php) ·
[norgatedata PyPI](https://pypi.org/project/norgatedata/) ·
[EODHD index constituents](https://eodhd.com/financial-apis-blog/index-constituents-or-index-components-api) ·
[EODHD delisted data](https://eodhd.com/financial-apis/delisted-stock-companies-data) ·
[Polygon historical-composition issue #129](https://github.com/polygon-io/issues/issues/129) ·
[FTSE Russell constituents/weights (LSEG)](https://www.lseg.com/en/ftse-russell/index-resources/constituent-weights) ·
[CRSP on WRDS](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/center-for-research-in-security-prices-crsp/)
