# Backtest Bug / Issue Log — 2025 Full-QIVC Run (Task 10)

Per the bug-finding protocol: every issue surfaced while wiring and running the
backtest, with root cause and resolution. Code bugs get their own commit + a
regression test; data/design findings are documented (and surfaced as OQs).

**Outcome:** the run surfaced **3 issues** (1 data prerequisite, 1 metric
artifact, 1 design finding) and **0 code bugs in the engine/signal** — the
integration ran clean on the first full run because wiring issues were caught in
smoke-testing during the build. No fix-commit was required *from the run itself*.

---

## Issue 1 — CMP history-coverage prerequisite (data; FIXED) — found in smoke-test

**Surfaced:** single-date smoke test of `qivc_signal_as_of` on 2025-04-01 →
**0 clusters**, all 169 window insiders "unclassified".

**Root cause:** CMP classifies a buy in year Y against the 3 prior calendar years
(Y-1, Y-2, Y-3). For 2025 that is 2022/2023/2024, but the bulk store started
**2023q1** — 2022 was absent, so no 2025 insider could reach 3 distinct prior
years → unclassifiable → no clusters → the backtest would hold 100% cash for a
content-free reason.

**Resolution:** bootstrapped **2022q1–q4** into the bulk store (the SEC Insider
Transactions Data Sets reach back to 2006). After the fix, 13 names cluster across
2025. No code change (bulk data is gitignored). Documented in STRATEGY_NOTES
("Daily Screen Operational Performance") and BACKTEST_LIMITATIONS §7. Covered by
the unit test `test_insufficient_history_is_unclassified_no_cluster`.

## Issue 2 — Degenerate Sharpe on an all-cash year (metric artifact; HANDLED)

**Surfaced:** the completed run reported **Sharpe 287** while holding 100% cash.

**Root cause:** Sharpe = mean/std·√252; a ~100%-T-bill portfolio has near-zero
daily volatility, so the ratio explodes. It is **not** skill and **not**
look-ahead — it is a degenerate statistic for a cash series.

**Resolution:** `engine.compute_metrics` emits a `sharpe_caveat` field whenever
the portfolio is ~all-cash / 0 trades, stating the Sharpe is a degenerate artifact
to disregard. Regression test `test_metrics_degenerate_all_cash_flagged`. This
pre-empts the ">Sharpe 2.0 ⇒ investigate look-ahead" tripwire: the high Sharpe
here is explained, not suspicious.

## Issue 3 — Quality core incompatible with the small-cap insider universe (design finding) — OQ-6

**Surfaced:** the full run produced **0 candidates / 0 trades** despite 13 names
clustering — every one rejected by the quality core (12 at F-Score, 1 at GP/A).

**Root cause (NOT a bug):** 8 of the 13 clustered names are **Financials/REITs**,
for which Piotroski F-Score and Novy-Marx GP/A are undefined (no gross-profit line
→ GP/A ≈ 0; F-Score margin/turnover/liquidity components don't apply). The gates
ran correctly on cleanly-fetched data and faithfully rejected them; biotech (BHVN)
and ed-tech (NRDY) failed on negative ROA; the one evaluable name (IMMR) failed
GP/A 8.3% < 25% legitimately.

**Resolution:** documented as **OQ-6** in STRATEGY_NOTES (v2.1 design question:
exclude financials, build a financials-specific quality model, or accept the
strategy won't trade financials). No code change — the behavior is faithful;
changing it would be a strategy change requiring a brief update.

## Non-issues verified
- Fundamentals fetched cleanly for all 17 (ticker, date) pairs (no nulls).
- All 12 monthly dossiers pass backtest-scoped sanity (vacuously — 0 candidates).
- Reproducibility: two cached re-runs produced byte-identical equity_curve.csv
  and metrics.json.
