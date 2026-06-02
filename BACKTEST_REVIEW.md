# QIVC v2.0 — 2025 Backtest Review (Task 10)

> ⚠️ **READ FIRST — caveats.** This is a **falsification exercise, not a
> validation.** It is **one observation from a sample of one**, over a single
> calendar year, and it produced **0 trades** (the strategy held T-bills all
> year). No performance metric here has any statistical power — the Sharpe SE is
> ±12.9 and the headline Sharpe (287) is a **degenerate artifact of holding
> ~100% cash** (near-zero volatility), NOT skill and NOT look-ahead. The value of
> this exercise is entirely **bug-finding and the qualitative review of which
> names the strategy surfaced and why it rejected them.** Do not use any number
> below to judge the strategy. Survivor/look-ahead caveats: see
> [BACKTEST_LIMITATIONS.md](BACKTEST_LIMITATIONS.md).

Run id `2025_full_qivc` · 2025-01-02 → 2025-12-31 · monthly rebalance · IWM
universe (snapshot 2026-06-01) · $1,000,000 · 5 bps slippage · 1 bp commission ·
seed 42 · **reproducible (byte-identical re-run verified).**

---

## 1. Executive summary

Run faithfully point-in-time, the full QIVC v2.0 strategy made **zero trades in
2025** and held T-bills the entire year (≈ **+4.08%**, vs IWN **+12.6%** and SPY
**+18.0%**). The CMP insider-cluster engine *did* fire — it surfaced **13 small-cap
names** across 10 of 12 months — but **every one was rejected by the quality
core** (12 at F-Score, 1 at GP/A). The dominant reason is structural: **8 of the
13 clustered names are Financials or REITs**, for which Piotroski F-Score and
Novy-Marx GP/A are not defined (banks/BDCs have no gross-profit line → GP/A ≈ 0;
the F-Score margin/turnover/liquidity components don't apply). This is the
backtest's central finding: **the names that exhibit opportunistic insider
clustering in small-caps are disproportionately financials/REITs, which QIVC's
quality core — built for non-financial operating companies — cannot pass.** The
exercise also surfaced one data-prerequisite (CMP for 2025 needs the bulk store
back to 2022; fixed) and confirmed three gates are UNVERIFIABLE for lack of PIT
data (valuation/revisions/short-interest; see OQ-5).

## 2. Headline metrics (with explicit standard errors — all statistically void)

| Metric | Value | Note |
|---|---|---|
| Total return | **+4.08%** | = T-bill accrual (100% cash all year) |
| IWN (benchmark) | +12.57% | strategy **lagged by 8.49 pts** |
| SPY (context) | +18.01% | lagged by ~13.9 pts |
| Sharpe | 287.3 | **degenerate** — ~0 vol cash; meaningless; **not look-ahead** |
| Sharpe standard error | **±12.91** | at this sample size, any Sharpe is noise |
| Sortino / Calmar / max DD | 0 / 0 / 0.0% | cash never drew down |
| Deflated Sharpe (DSR) | **null** | one trial → trial count undefined (by design) |
| PSR vs 0 | 1.0 | degenerate (cash always positive) |
| Beta vs IWN | ~0.0 | no equity exposure |
| Total trades | **0** | 12/12 months zero candidates |
| Avg cash | 100% | — |

**The only honest reading of this table: the portfolio was cash, so it earned the
cash rate and lagged a rising small-cap market. Everything else is noise.**

## 3. Names held

**None.** The strategy held **zero equities** in 2025 — 100% T-bills every month.
The substantive review is therefore of the names *surfaced* (clustered) and
rejected (§4).

## 4. Names surfaced (clustered) and which gate killed each

13 distinct names clustered in 2025 (the CMP engine worked). Every one was
rejected by the quality core. "2025 %" is the name's own start→end price move
(not a position — we never held it).

| Ticker | Sector | What it is | Why surfaced | Killed by | 2025 % |
|---|---|---|---|---|---|
| UAMY | Materials | US Antimony (mining) | Track B, 1 officer | F-Score (ROA −5%) | **+190.2** |
| ATLO | Financials | Ames National (bank) | Track A, 3 insiders | F-Score (GP/A 0 — bank) | +46.0 |
| CWBC | Financials | Community West Bank | Track A, 4 insiders | F-Score (GP/A 0 — bank) | +21.1 |
| UHT | Real Estate | Universal Health Realty (REIT) | Track B, 1 officer | F-Score (REIT) | +15.5 |
| NREF | Financials | NexPoint Real Estate Finance | Track B, 1 officer | F-Score (GP/A 0) | +1.9 |
| MBIN | Financials | Merchants Bancorp | Track B, 1 officer | F-Score (GP/A 0 — bank) | −4.3 |
| BRT | Real Estate | BRT Apartments (REIT) | Track A, 3 insiders (3 mo.) | F-Score (ROA −1.4%) | −11.3 |
| PEB | Real Estate | Pebblebrook Hotel Trust (REIT) | Track B, 1 officer | F-Score (ROA ~0) | −14.4 |
| IMMR | Info Tech | Immersion (haptics licensing) | Track B, 1 officer | **GP/A 8.3% < 25%** | −17.6 |
| SUNS | Financials | Sunoco / SLR-Senior (BDC) | Track B, 1 officer (3 mo.) | F-Score (GP/A 0 — BDC) | −25.8 |
| NRDY | Consumer Disc | Nerdy (ed-tech) | Track B, 1 officer | F-Score (ROA −46%) | −33.8 |
| ONEW | Consumer Disc | OneWater Marine (boat retail) | Track B, 1 officer | F-Score (ROA ~0) | −37.0 |
| BHVN | Health Care | Biohaven (biotech) | Track A, 3 insiders | F-Score (ROA −138%) | −69.7 |

**Recognizability / read-through.** These are genuine small-cap insider-conviction
events — e.g. ATLO/CWBC/MBIN community-bank insiders buying, a Biohaven cluster,
US Antimony (which then ran +190%). But the *signal alone* was not cleanly
predictive in 2025: an equal-weight basket of all 13 would have returned ≈ **+4.6%**
(dominated entirely by the UAMY +190% outlier; the median name was **−14%**). So
the quality core's rejection did not obviously forgo alpha — it mostly screened
out names that fell. The one name with real, evaluable fundamentals (IMMR, a
profitable tech licensor) was rejected for genuinely low gross profitability
(GP/A 8.3% vs the 25% bar), and it fell −18%.

## 5. Notable misses

The research brief mentioned 2025 insider events at **Nike, Abbott, Intel,
UnitedHealth, Lululemon**. **None could ever appear** in this backtest: all are
**large-caps, not Russell 2000 (IWM) constituents**, so they are outside the
universe by construction. They were not "killed by a gate" — they were never
eligible. (A large-cap or all-cap universe is a separate study; this run is
IWM-scoped per spec.)

## 6. Sector concentration analysis

Realized portfolio concentration was **0% in every sector** (100% cash). Among the
*surfaced-and-rejected* names, the clustering pool was heavily **Financials
(5/13) + Real Estate (3/13) = 62%** — the exact sectors the quality core cannot
evaluate. Consumer Discretionary (2), and one each of Materials, Info Tech, Health
Care. The 20% sector / 12% sub-sector caps never bound (no candidates to cap).

## 7. Comparison to expected regime behavior

- 2025: SPY ≈ +18%, Russell 2000 ≈ +12% (IWN +12.6% realized here).
- Expectation: a value-tilted quality strategy should **lag SPY** and **roughly
  match or slightly beat IWN**.
- Realized: the strategy returned **+4.08% (cash), lagging IWN by 8.5 pts.** It
  did **not** beat SPY (no look-ahead red flag on return; +4.08% < 25%).
- **Is this "broken"?** By the ">10% lag vs IWN ⇒ possibly broken" heuristic, an
  8.5-pt lag is within tolerance and is fully **explained**: the strategy made no
  trades because the quality core rejected every clustered name. This is **coherent,
  conservative behavior** (QIVC is designed to hold nothing rather than hold names
  it cannot verify), not a malfunction — but it does expose that the quality core
  and the small-cap insider universe barely intersect (OQ-6).
- **Regime:** all 12 months classified **risk-on** — the VIX 60-day SMA smooths
  April's ~2-week tariff-shock spike below the 25/35 thresholds, so the regime
  gate never blocked (and never needed to, since there was nothing to size).

## 8. Bugs / issues found during the run

Full log in [BACKTEST_BUGS.md](BACKTEST_BUGS.md). Summary:
1. **Data-coverage prerequisite (fixed):** CMP for a 2025 buy needs 2022/2023/2024
   history; the store started 2023q1 → every 2025 insider unclassifiable → 0
   clusters. Fixed by bootstrapping 2022 (no code change; bulk data).
2. **Three gates UNVERIFIABLE (confirmed, documented):** valuation (sector-median
   pipeline is a stub — OQ-5), revisions, short_interest have no PIT source.
   Backtest scopes candidacy/sanity to the 5 binding gates.
3. **Degenerate Sharpe (handled):** all-cash year → Sharpe explodes; flagged with
   `sharpe_caveat` in metrics.json so it is never mistaken for skill or look-ahead.
4. **No code bugs in the engine/signal** — the integration ran clean (the
   smoke-test-driven build caught wiring issues before the full run). No fix-commits
   were required from the run itself.

## 9. Open questions raised

- **OQ-6 (new, STRATEGY_NOTES):** QIVC's quality core (F-Score, GP/A) is
  **structurally incompatible with financials/REITs**, which dominate small-cap
  insider clustering. Should the strategy (a) exclude financials from the
  universe, (b) use a financials-specific quality model, or (c) accept that it
  simply won't trade financials? At 1-year scope this caused 0 trades.
- Would an **all-cap / large-cap** universe (where the brief's Nike/Abbott/etc.
  events live) produce candidates the quality core *can* evaluate?
- Is the **opportunistic insider signal predictive on its own** in small-caps? The
  13-name basket was +4.6% (UAMY-driven), median −14% — weak evidence, tiny sample.

---

> ⚠️ **Caveats (repeated).** One observation, one year, **zero trades**. No metric
> here is statistically meaningful (Sharpe SE ±12.9; headline Sharpe is a cash
> artifact). This exercise was for **bug-finding and qualitative review**, both of
> which it served. It does **not** validate or invalidate the strategy. See
> [BACKTEST_LIMITATIONS.md](BACKTEST_LIMITATIONS.md) for survivor bias, the three
> UNVERIFIABLE gates, the current-shares/sector approximations, and the
> sector-median look-ahead.
