# QIVC v3.0 — Grid Cross-Validation Review

> ⚠️ **READ FIRST.** Falsification exercise, not validation. Every per-year number
> is **one observation** (8–44 trades/yr — below any significance floor). The
> **Phase 4A (2025) results are NOT forward-credible: they are dominated by
> survivor/membership bias** (see below). Do not read the high returns as edge.

---

## Part A — 2025, all 18 configs (Phase 4A)

18 configs = holding {30,90,180}d × entry-threshold {60,75,90}pct × N {5,10}.
Universe IWM-ex-Financials/REITs (snapshot **2026-06-01**), monthly rebalance,
$1M, 5bps slippage / 1bp commission, seed 42. Reproducible (byte-identical
re-run verified). All dossiers pass v3.0 sanity (0 failures).

| Config | Trades | Return% | Sharpe | MaxDD% | Sortino | AvgHeld | Cash% | ZeroMo |
|---|---|---|---|---|---|---|---|---|
| N5_thr60_hold30 | 25 | 22.05 | 0.8 | −17.53 | 1.20 | 4.00 | 8.3 | 1 |
| N10_thr60_hold30 | 44 | 27.75 | 0.9 | −24.00 | 1.58 | 7.75 | 8.3 | 1 |
| N5_thr75_hold30 | 25 | 22.05 | 0.8 | −17.53 | 1.20 | 4.00 | 8.3 | 1 |
| N10_thr75_hold30 | 42 | 37.35 | 1.1 | −24.00 | 1.96 | 7.25 | 8.3 | 1 |
| N5_thr90_hold30 | 20 | 69.21 | 1.8 | −19.62 | 2.92 | 3.00 | 8.3 | 1 |
| N10_thr90_hold30 | 28 | 74.15 | 1.9 | −19.77 | 3.10 | 4.17 | 8.3 | 1 |
| N5_thr60_hold90 | 17 | 38.29 | 1.1 | −24.20 | 1.88 | 4.00 | 8.3 | 1 |
| N10_thr60_hold90 | 32 | 37.75 | 1.2 | −24.00 | 2.04 | 7.92 | 8.3 | 1 |
| N5_thr75_hold90 | 17 | 38.29 | 1.1 | −24.20 | 1.88 | 4.00 | 8.3 | 1 |
| N10_thr75_hold90 | 32 | 37.75 | 1.2 | −24.00 | 2.04 | 7.92 | 8.3 | 1 |
| N5_thr90_hold90 | 15 | 58.62 | 1.8 | −19.10 | 2.98 | 3.33 | 8.3 | 1 |
| N10_thr90_hold90 | 25 | 66.73 | 2.0 | −19.03 | 3.11 | 5.83 | 8.3 | 1 |
| N5_thr60_hold180 | 10 | 30.06 | 1.0 | −19.36 | 1.62 | 4.00 | 8.3 | 1 |
| N10_thr60_hold180 | 20 | 24.88 | 0.9 | −24.36 | 1.43 | 7.75 | 8.3 | 1 |
| N5_thr75_hold180 | 10 | 30.06 | 1.0 | −19.36 | 1.62 | 4.00 | 8.3 | 1 |
| N10_thr75_hold180 | 20 | 23.45 | 0.8 | −26.29 | 1.31 | 7.58 | 8.3 | 1 |
| N5_thr90_hold180 | 9 | 21.31 | 0.8 | −19.10 | 1.27 | 3.58 | 8.3 | 1 |
| N10_thr90_hold180 | 18 | 67.44 | 2.0 | −19.03 | 3.14 | 6.75 | 8.3 | 1 |

**Aggregate:** median **+37.75%**, range **[+21.3%, +74.1%]**, **18/18 positive**.
Benchmarks: IWN **+12.6%**, SPY **+18.0%** (2025 was growth-led). Higher entry
threshold → higher return (thr90 ≫ thr60), because tighter selection → more
concentration in the survivor-winners.

### ⚠️ Red-flag investigation (mandatory, done BEFORE reporting)
Every config beat SPY by 4–56 points — your >25%/Sharpe>2.0 tripwire. I audited:
- **Entries are point-in-time correct.** Spot-checked CELC: entered at the
  **2025-10-01 close (45.22)**, ran to 75.19 by 11-03 (+66% *during* the hold) —
  the insider signal (opportunistic buys filed before 10-01) preceded the move.
- **One code look-ahead found and fixed** (own commit `8f7081d`): the price matrix
  used `bfill`, which would backfill a 2025 IPO's pre-listing dates with future
  prices. Fixed to ffill-only + drop untradeable selections. **Re-run after the
  fix: byte-identical results** → the bug affected only one name (MPLT) and was
  **not** the driver.
- **The driver is survivor/membership bias.** The universe is the **2026-06 IWM
  snapshot applied to 2025**, so the concentrated 1–8 name book systematically
  holds small-cap names that had insider buying **and survived to 2026** (CELC
  +660%, ZBIO +303%, UAMY +190%, KYMR/MDGL/KALV/TYRA +80–90% full-year). Insider-
  bought names that crashed to delisting are **absent from the universe**. The
  held set's *full-year* returns are actually a mixed bag (CDXS −67%, JELD −70%,
  BRCC −65%, ONEW −37%), but the survivor pool tilts the book upward.
- **Deflated Sharpe (Bailey–López de Prado 2014, 18 trials):** best raw annualized
  Sharpe **2.02**; expected-max Sharpe under the null across 18 trials **≈0.79**;
  **DSR ≈ 0.91** (T=12 months) — the raw ~2.0 deflates heavily on trial count
  alone, *before* even accounting for survivor bias.

**Conclusion: the 2025 numbers do not constitute evidence of edge.** They are a
survivor-bias artifact on a single, statistically void observation.

### Names surfaced (union across all 18 configs): 41
AAOI, BATRA, BRCC, CDXS, CELC, CVI, EML, GSAT, GWRS, HTLD, IMMR, JAKK, JELD, KALV,
KNTK, KYMR, LAB, MBX, MDGL, MPLT, NRDY, OEC, ONEW, PAMT, PRME, REPX, RIG, SEI,
SLSN, SONO, STAA, STGW, TDW, TRDA, TYRA, UAMY, VERA, VRDN, WDFC, ZBIO, ZYME.
(Mixed full-year outcomes — the strategy's positive 2025 is a survivor-pool +
concentration effect, not name-selection skill.)

### Concentration (confirms the Phase-2 finding)
Scored (insider-active) names per month: ~28–42; held 1–8. The book is naturally
concentrated; the 30% sector cap rarely binds; N=5 vs N=10 differ only modestly.

### Critical implication for Part B (2022–2024)
**Survivor bias is not specific to 2025 — it affects every year**, because the
2026-06 IWM membership snapshot is applied to all of 2022–2025. The planned
in-sample (2022–24) / out-of-sample (2025) split **does not escape survivor
bias**: a config that looks "robust across years" may simply be robustly exposed
to survivors in each year. Point-in-time index membership (historical Russell 2000
constituents per year) would be required to make the cross-validation
trustworthy. **This is a decision point — see the report.**

(Part B and the full cross-validation / DSR-per-config / OOS analysis are Phase 5,
pending the survivor-bias decision.)
