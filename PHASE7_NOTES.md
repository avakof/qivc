# Phase 7 Notes — Backtesting Harness

## Acceptance gate results

| Gate | Result |
|---|---|
| Backtest runs end-to-end on a small range | **PASS** — live 3-ticker / 3-month run produced equity_curve.csv, metrics.json, trades.csv; also covered by synthetic integration test |
| Output metrics reproducible (same seed → same numbers) | **PASS** — `test_backtest_reproducible` asserts identical equity curve, metrics, and trades across two runs |
| No look-ahead bias in PIT functions (verified by tests) | **PASS** — `test_pit.py` incl. a hypothesis property test: nothing filed on/after the decision date ever appears in the as-of set |

Live small-range run (`qivc backtest --start 2024-01-01 --end 2024-03-31 --universe AAPL,MSFT,JNJ`):
CAGR 40.6%, Sharpe 2.36, Sortino 2.39, max DD −3.2%, Calmar 12.6, hit-rate 1.0,
alpha vs IWN 20.9%, final equity $1,083,950.

## Gate-discovered fix (committed separately)

The live full-screen gate (`qivc screen`, no `--ticker`) surfaced a real bug in
the never-before-exercised market-wide Form 4 path: it called
`edgar.search_filings(forms="4")`, which requires a query/items filter. Fixed to
`edgar.get_filings(form="4", filing_date=range)` with a bounded parse cap. See
commit `fix(data): market-wide Form 4 scan uses get_filings, not EFTS search`.

## Dependency resolution: vectorbt

`vectorbt==1.0.0` **did** install on Python 3.13 / numpy 2.4, but pulled
pandas 2.3.3 (down from 3.0.3). The full pre-existing suite (266 tests) still
passes on pandas 2.3.3, so the downgrade was accepted and vectorbt is used for
the portfolio simulation (`Portfolio.from_orders`, target-percent sizing, fees,
slippage, fixed seed). Per your instruction, the hand-roll fallback was only to
be used if vectorbt failed to install — it didn't, so vectorbt is in.

The reported metrics (CAGR/Sharpe/Sortino/maxDD/Calmar/hit-rate/hold/alpha) are
computed in `qivc.backtest.metrics` with plain numpy/pandas, independent of
vectorbt's internal conventions, so they are deterministic and directly
unit-tested with hand-checked values.

## Architecture

- **`pit.py`** — point-in-time access. `filter_by_filing_date` (pure, the
  no-look-ahead core) keeps only filings dated strictly before `as_of`.
  `get_form4_as_of` / `get_fundamentals_as_of` apply this over live EDGAR.
- **`harness.py`** — `run_backtest(...)` walks rebalance dates, calls an
  injectable `screen_fn(as_of) -> [tickers]`, builds an equal-weight
  target-percent matrix, simulates with vectorbt, extracts round-trip trades,
  and computes metrics. `screen_fn` + `price_data` injection makes it fully
  deterministic and testable.
- **`metrics.py`** — pure metric functions + `compute_all`.
- **`io.py`** — writes equity_curve.csv, metrics.json, trades.csv.
- **CLI** — `qivc backtest --start --end [--freq W|M] [--universe ...]`.

## Documented limitations (in `BacktestResult.notes` and code)

1. **Sector-median look-ahead** — historical sector-ETF constituents are
   unavailable, so `get_sector_median_as_of` returns None; the valuation gate
   can't be evaluated PIT. `SECTOR_MEDIAN_LOOKAHEAD_NOTE` is attached to every
   result. (Brief §11.)
2. **Survivor bias** — the backtest universe is the `--universe` list of
   currently-listed tickers; de-listed names are absent (no CRSP). Brief §5
   Phase 7 flags this; documented as a known limitation.
3. **Quality-core screen for the live CLI** — full insider-cluster backtesting
   needs 3-year historical Form 4 bulk dumps (`bootstrap_bulk_data.py` is a
   stub). The live `qivc backtest` screen therefore holds names passing the
   Piotroski F-Score ≥ 7 quality gate on PIT fundamentals; the full harness
   accepts any `screen_fn`, so the complete screen can be wired once bulk Form 4
   history is loaded.
4. **Market-wide Form 4 daily scan is API-bounded** (100 filings/run); the
   production path should use the SEC bulk dumps (Brief §3.2).
