# Backtest Limitations — 2025 Full-QIVC Run (Task 10)

This is the look-ahead / bias audit for the 2025 backtest. Read alongside
[BACKTEST_REVIEW.md](BACKTEST_REVIEW.md). The run produced **0 trades**, so most
of these limitations did not actually bear on a P&L — but they define what the
backtest *could* have measured and must qualify any future, longer run.

## 1. Sample size (the overriding limitation)

One calendar year, monthly rebalance, **0 trades**. The Sharpe standard error is
**±12.9**. Any performance number is **one observation from a sample of one** and
has no statistical power. The headline Sharpe (287) is a **degenerate artifact of
a ~100%-cash portfolio** (near-zero volatility), flagged in `metrics.json`
(`sharpe_caveat`). This is **not** evidence of look-ahead.

## 2. Survivor & index-membership bias (upward)

The universe is the **iShares IWM (Russell 2000) holdings as of 2026-06-01**,
applied to a 2025 backtest. Names that were in the index during 2025 but have
since been delisted/acquired/dropped are **absent**; names added after 2025 are
**wrongly included**. Both biases are upward (we screen current survivors). No
CRSP/point-in-time membership was used.

## 3. Gates with NO point-in-time data → UNVERIFIABLE (do not bind)

Three of the eight production gates have no free PIT source and ran UNVERIFIABLE
(they neither rejected nor used current values — avoiding look-ahead):

| Gate | Why UNVERIFIABLE |
|---|---|
| valuation | No PIT sector medians exist — `refresh_sector_medians.py` is a stub (OQ-5). Using *current* medians would be look-ahead; not done. |
| revisions | No free PIT consensus-EPS snapshots (yfinance gives only current). |
| short_interest | No free PIT short-interest history (yfinance gives only current). |

The backtest therefore scopes candidacy and sanity to the **5 binding gates that
DO have PIT data: regime, insider_conviction, F-Score, GP/A, liquidity.** This is
a backtest-scoped relaxation of production's 8-gate `REQUIRED_GATES`, not a
strategy change (see STRATEGY_NOTES OQ-5).

## 4. Sector-median look-ahead (moot here, would matter in a trading run)

Even where valuation *could* run, sector medians are not point-in-time. In this
run valuation was UNVERIFIABLE so it never affected an entry, but any future run
that activates valuation must treat current-median substitution as look-ahead.

## 5. Liquidity uses current shares outstanding (minor)

Market cap = PIT price × **current** shares-outstanding (yfinance.info). Shares
are slowly-varying, so this is a minor approximation, but it is technically
look-ahead on the share count. ADV is fully PIT (PIT price × PIT volume). The
$300M market-cap floor is a binding gate, so this approximation could in principle
flip a borderline name — none were close in this run.

## 6. Industry classification is current (cap bucketing only)

Sub-sector (GICS industry) for the 12% sub-sector cap comes from current
yfinance.info. It is a classification label, not a P&L input, and the cap never
bound (0 candidates).

## 7. History-coverage prerequisite

CMP classification at date D requires the bulk store to extend back to **D − 3
calendar years**. The 2025 run required bootstrapping **2022** (the store began
2023q1). Documented in STRATEGY_NOTES ("Daily Screen Operational Performance").
Any backtest before 2023 needs earlier quarters bootstrapped first.

## 8. Fills, costs, and the cash model

- Fills at the rebalance day's **close** (vectorbt target-percent), 5 bps slippage
  + 1 bp commission. No intraday, no market-impact beyond slippage, no borrow.
- Cash residual is parked in a synthetic **T-bill asset** compounding at ^IRX
  (13-week yield). This is a reasonable cash proxy, not an actual bill ladder.
- Reproducibility: all external data is disk-cached; the cache (not the live
  endpoint) is the reproducibility anchor. Re-runs are byte-identical (verified).

## 9. What this backtest does NOT establish

It does **not** validate or invalidate QIVC. With 0 trades it cannot speak to the
strategy's return, risk, or edge. It established three things only: (a) the PIT
plumbing works end-to-end and is reproducible; (b) the small-cap insider-cluster
universe is dominated by financials/REITs the quality core can't evaluate (OQ-6);
(c) three gates lack PIT data (OQ-5). Those are the deliverables.
