# Phase 4A — Winner/Loser DESCRIPTIVE Analysis (`v3_2025_N10_thr90_hold30`)

> ⚠️ **THIS IS NOT VALIDATION.** Exploratory description of a single config's 28 trades from one survivor-biased year. **n = 22 closed trades** — below any statistical-significance floor. No hypothesis tests are run or implied. Every pattern below is a **HYPOTHESIS to test later** under point-in-time cross-validation, not a feature, not evidence of edge. Recall Task 11: composite-vs-return R^2 = 0.004.

## Why this is not validation (read first)

- **Survivor/membership bias:** universe = 2026-06 IWM snapshot applied to 2025; names that delisted are absent. Winners are conditioned on survival.
- **n=22 closed** (12 win / 10 loss): one moderate winner moves every median. No power to separate signal from noise.
- **One year, one regime** (2025 small-cap tape). No cross-section over time.
- **mcap@entry approximate:** current shares x entry price (shares drift). `ret_2024` is backward context, not a tradeable signal.
- **valuation & momentum run neutral (0.5)** in v3.0 — their columns carry no information here; do not read them.

## Full feature table (28 trades, entry order)

| ticker | sector | entry_month | hold_days | return_pct | outcome | composite | insider | quality | valuation | momentum | prior_trades_2025 | prior_was_loss | mcap_entry_$M | ret_2024_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BATRA | Communication | 2025-02 | 57.0 | -0.5 | LOSS | 0.608 | 0.75 | 0.528 | 0.5 | 0.5 | 0 |  | 453.0 | -3.5 |
| KALV | Health Care | 2025-03 | 59.0 | 25.11 | WIN | 0.626 | 0.971 | 0.292 | 0.5 | 0.5 | 0 |  | 579.0 | -30.3 |
| TDW | Energy | 2025-04 | 62.0 | -6.28 | LOSS | 0.718 | 0.75 | 0.894 | 0.5 | 0.5 | 0 |  | 2136.0 | -22.1 |
| STAA | Health Care | 2025-04 | 91.0 | -3.3 | LOSS | 0.761 | 0.988 | 0.72 | 0.5 | 0.5 | 0 |  | 876.0 | -18.3 |
| IMMR | Information Technology | 2025-04 | 62.0 | -3.4 | LOSS | 0.739 | 0.869 | 0.806 | 0.5 | 0.5 | 0 |  | 246.0 | 25.9 |
| GSAT | Communication | 2025-04 | 122.0 | 11.78 | WIN | 0.732 | 0.964 | 0.653 | 0.5 | 0.5 | 0 |  | 2667.0 | 10.7 |
| ZYME | Health Care | 2025-04 | 30.0 | 10.84 | WIN | 0.656 | 0.94 | 0.434 | 0.5 | 0.5 | 0 |  | 849.0 | 41.0 |
| BATRA | Communication | 2025-05 | 32.0 | 0.88 | WIN | 0.659 | 0.971 | 0.403 | 0.5 | 0.5 | 1 | True | 447.0 | -3.5 |
| CVI | Energy | 2025-05 | 61.0 | 44.03 | WIN | 0.679 | 0.856 | 0.623 | 0.5 | 0.5 | 0 |  | 1939.0 | -36.7 |
| JELD | Industrials | 2025-06 | 60.0 | 25.14 | WIN | 0.682 | 0.935 | 0.528 | 0.5 | 0.5 | 0 |  | 312.0 | -55.9 |
| OEC | Materials | 2025-06 | 60.0 | -12.75 | LOSS | 0.715 | 0.75 | 0.882 | 0.5 | 0.5 | 0 |  | 595.0 | -40.9 |
| STGW | Communication | 2025-06 | 92.0 | 34.38 | WIN | 0.705 | 0.824 | 0.75 | 0.5 | 0.5 | 0 |  | 1031.0 | -1.1 |
| TYRA | Health Care | 2025-07 | 63.0 | 28.91 | WIN | 0.697 | 0.989 | 0.505 | 0.5 | 0.5 | 0 |  | 578.0 | -1.0 |
| TDW | Energy | 2025-07 | 31.0 | -0.36 | LOSS | 0.671 | 0.641 | 0.882 | 0.5 | 0.5 | 1 | True | 2372.0 | -22.1 |
| SLSN | Materials | 2025-07 | 31.0 | -35.94 | LOSS | 0.676 | 0.728 | 0.783 | 0.5 | 0.5 | 0 |  | 307.0 | 306.7 |
| CDXS | Health Care | 2025-08 | 61.0 | -7.06 | LOSS | 0.684 | 0.967 | 0.49 | 0.5 | 0.5 | 0 |  | 245.0 | 52.9 |
| BRCC | Consumer Staples | 2025-08 | 32.0 | -6.1 | LOSS | 0.691 | 0.789 | 0.75 | 0.5 | 0.5 | 0 |  | 192.0 | -24.7 |
| REPX | Energy | 2025-08 | 32.0 | 18.35 | WIN | 0.792 | 0.944 | 0.882 | 0.5 | 0.5 | 0 |  | 529.0 | 25.8 |
| MDGL | Health Care | 2025-09 | 62.0 | -5.45 | LOSS | 0.666 | 0.95 | 0.454 | 0.5 | 0.5 | 0 |  | 10055.0 | 36.0 |
| SONO | Consumer Discretionary | 2025-09 | 62.0 | 22.34 | WIN | 0.702 | 0.983 | 0.528 | 0.5 | 0.5 | 0 |  | 1637.0 | -9.8 |
| CELC | Health Care | 2025-10 | 33.0 | 66.28 | WIN | 0.632 | 0.768 | 0.583 | 0.5 | 0.5 | 0 |  | 2205.0 | -11.4 |
| RIG | Energy | 2025-10 | 61.0 | 34.67 | WIN | 0.701 | 0.982 | 0.528 | 0.5 | 0.5 | 0 |  | 3575.0 | -40.0 |
| VRDN | Health Care | 2025-11 |  |  | OPEN | 0.688 | 0.919 | 0.566 | 0.5 | 0.5 | 0 |  | 2556.0 | -14.2 |
| KNTK | Energy | 2025-11 |  |  | OPEN | 0.66 | 0.629 | 0.861 | 0.5 | 0.5 | 0 |  | 2696.0 | 81.5 |
| MPLT | Health Care | 2025-11 |  |  | OPEN | 0.681 | 0.952 | 0.5 | 0.5 | 0.5 | 0 |  | 721.0 |  |
| ONEW | Consumer Discretionary | 2025-12 |  |  | OPEN | 0.686 | 0.808 | 0.708 | 0.5 | 0.5 | 0 |  | 188.0 | -47.4 |
| TRDA | Health Care | 2025-12 |  |  | OPEN | 0.686 | 0.756 | 0.78 | 0.5 | 0.5 | 0 |  | 385.0 | 14.0 |
| BATRA | Communication | 2025-12 |  |  | OPEN | 0.683 | 0.936 | 0.528 | 0.5 | 0.5 | 2 | False | 445.0 | -3.5 |

## Grouped: winners

| ticker | sector | entry_month | hold_days | return_pct | composite | insider | quality | valuation | momentum | prior_trades_2025 | prior_was_loss | mcap_entry_$M | ret_2024_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KALV | Health Care | 2025-03 | 59.0 | 25.11 | 0.626 | 0.971 | 0.292 | 0.5 | 0.5 | 0 |  | 579.0 | -30.3 |
| GSAT | Communication | 2025-04 | 122.0 | 11.78 | 0.732 | 0.964 | 0.653 | 0.5 | 0.5 | 0 |  | 2667.0 | 10.7 |
| ZYME | Health Care | 2025-04 | 30.0 | 10.84 | 0.656 | 0.94 | 0.434 | 0.5 | 0.5 | 0 |  | 849.0 | 41.0 |
| BATRA | Communication | 2025-05 | 32.0 | 0.88 | 0.659 | 0.971 | 0.403 | 0.5 | 0.5 | 1 | True | 447.0 | -3.5 |
| CVI | Energy | 2025-05 | 61.0 | 44.03 | 0.679 | 0.856 | 0.623 | 0.5 | 0.5 | 0 |  | 1939.0 | -36.7 |
| JELD | Industrials | 2025-06 | 60.0 | 25.14 | 0.682 | 0.935 | 0.528 | 0.5 | 0.5 | 0 |  | 312.0 | -55.9 |
| STGW | Communication | 2025-06 | 92.0 | 34.38 | 0.705 | 0.824 | 0.75 | 0.5 | 0.5 | 0 |  | 1031.0 | -1.1 |
| TYRA | Health Care | 2025-07 | 63.0 | 28.91 | 0.697 | 0.989 | 0.505 | 0.5 | 0.5 | 0 |  | 578.0 | -1.0 |
| REPX | Energy | 2025-08 | 32.0 | 18.35 | 0.792 | 0.944 | 0.882 | 0.5 | 0.5 | 0 |  | 529.0 | 25.8 |
| SONO | Consumer Discretionary | 2025-09 | 62.0 | 22.34 | 0.702 | 0.983 | 0.528 | 0.5 | 0.5 | 0 |  | 1637.0 | -9.8 |
| CELC | Health Care | 2025-10 | 33.0 | 66.28 | 0.632 | 0.768 | 0.583 | 0.5 | 0.5 | 0 |  | 2205.0 | -11.4 |
| RIG | Energy | 2025-10 | 61.0 | 34.67 | 0.701 | 0.982 | 0.528 | 0.5 | 0.5 | 0 |  | 3575.0 | -40.0 |

## Grouped: losers

| ticker | sector | entry_month | hold_days | return_pct | composite | insider | quality | valuation | momentum | prior_trades_2025 | prior_was_loss | mcap_entry_$M | ret_2024_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BATRA | Communication | 2025-02 | 57.0 | -0.5 | 0.608 | 0.75 | 0.528 | 0.5 | 0.5 | 0 |  | 453.0 | -3.5 |
| TDW | Energy | 2025-04 | 62.0 | -6.28 | 0.718 | 0.75 | 0.894 | 0.5 | 0.5 | 0 |  | 2136.0 | -22.1 |
| STAA | Health Care | 2025-04 | 91.0 | -3.3 | 0.761 | 0.988 | 0.72 | 0.5 | 0.5 | 0 |  | 876.0 | -18.3 |
| IMMR | Information Technology | 2025-04 | 62.0 | -3.4 | 0.739 | 0.869 | 0.806 | 0.5 | 0.5 | 0 |  | 246.0 | 25.9 |
| OEC | Materials | 2025-06 | 60.0 | -12.75 | 0.715 | 0.75 | 0.882 | 0.5 | 0.5 | 0 |  | 595.0 | -40.9 |
| TDW | Energy | 2025-07 | 31.0 | -0.36 | 0.671 | 0.641 | 0.882 | 0.5 | 0.5 | 1 | True | 2372.0 | -22.1 |
| SLSN | Materials | 2025-07 | 31.0 | -35.94 | 0.676 | 0.728 | 0.783 | 0.5 | 0.5 | 0 |  | 307.0 | 306.7 |
| CDXS | Health Care | 2025-08 | 61.0 | -7.06 | 0.684 | 0.967 | 0.49 | 0.5 | 0.5 | 0 |  | 245.0 | 52.9 |
| BRCC | Consumer Staples | 2025-08 | 32.0 | -6.1 | 0.691 | 0.789 | 0.75 | 0.5 | 0.5 | 0 |  | 192.0 | -24.7 |
| MDGL | Health Care | 2025-09 | 62.0 | -5.45 | 0.666 | 0.95 | 0.454 | 0.5 | 0.5 | 0 |  | 10055.0 | 36.0 |

## Still open at year-end (excluded from win/loss)

| ticker | sector | entry_month | hold_days | return_pct | composite | insider | quality | valuation | momentum | prior_trades_2025 | prior_was_loss | mcap_entry_$M | ret_2024_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| VRDN | Health Care | 2025-11 |  |  | 0.688 | 0.919 | 0.566 | 0.5 | 0.5 | 0 |  | 2556.0 | -14.2 |
| KNTK | Energy | 2025-11 |  |  | 0.66 | 0.629 | 0.861 | 0.5 | 0.5 | 0 |  | 2696.0 | 81.5 |
| MPLT | Health Care | 2025-11 |  |  | 0.681 | 0.952 | 0.5 | 0.5 | 0.5 | 0 |  | 721.0 |  |
| ONEW | Consumer Discretionary | 2025-12 |  |  | 0.686 | 0.808 | 0.708 | 0.5 | 0.5 | 0 |  | 188.0 | -47.4 |
| TRDA | Health Care | 2025-12 |  |  | 0.686 | 0.756 | 0.78 | 0.5 | 0.5 | 0 |  | 385.0 | 14.0 |
| BATRA | Communication | 2025-12 |  |  | 0.683 | 0.936 | 0.528 | 0.5 | 0.5 | 2 | False | 445.0 | -3.5 |

## Group medians (DESCRIPTIVE — do not infer)

| group | n | median_composite | median_insider | median_quality | median_hold | median_mcap_$M | median_ret2024_pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| winners | 12.0 | 0.69 | 0.954 | 0.528 | 60.5 | 940.0 | -6.6 |
| losers | 10.0 | 0.688 | 0.77 | 0.766 | 60.5 | 524.0 | -10.9 |

## Descriptive patterns visible (HYPOTHESES — no tests, n is tiny)

Each item is something the eye picks out in 22 closed trades. None is tested; each is a **candidate to test under PIT cross-validation**, not a finding.

1. **The composite does not separate winners from losers.** Median composite is 0.69 (win) vs 0.688 (loss) — effectively identical. This is the same message as Task 11's R^2=0.004, now visible at the trade level: ranking by composite did not rank by outcome.
2. **Winners and losers reach the same composite via OPPOSITE factor mixes.** Winners: high insider (median 0.954) / low quality (median 0.528). Losers: lower insider (0.77) / high quality (0.766). The 40/30 blend averaged a possible insider-conviction tilt away. *Hypothesis:* insider conviction may carry more signal than the 40% weight captures, and Piotroski/GP-A quality may be miscalibrated — or even contrarian — in this small-cap insider universe. (Counterexamples exist: STAA, CDXS, MDGL are high-insider losers — so this is weak and noisy.)
3. **Many winners were beaten-down in 2024.** CVI (-37%), JELD (-56%), RIG (-40%), KALV (-30%) all fell hard in 2024, drew insider buying, then rose in 2025. Both groups skew negative-2024 (winners median -6.6%, losers -10.9%). *Hypothesis:* beaten-down-prior-year + insider buying => mean-reversion bounce. (Counterexample: SLSN +307% in 2024 -> -36% loss.)
4. **Winners skew larger-cap** (median mcap ~$940M vs ~$524M). *Hypothesis:* a size effect — but MDGL (~$10B) was a loser, so this is fragile.
5. **Hold days and sector show no visible separation.** Median hold is 60.5 days for both groups. Energy and Health Care each appear among both winners and losers.

## Repeat-entry pattern (BATRA, TDW)

| ticker | entry_month | outcome | return_pct | composite | insider | prior_trades_2025 | prior_was_loss |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BATRA | 2025-02 | LOSS | -0.5 | 0.608 | 0.75 | 0 |  |
| TDW | 2025-04 | LOSS | -6.28 | 0.718 | 0.75 | 0 |  |
| BATRA | 2025-05 | WIN | 0.88 | 0.659 | 0.971 | 1 | True |
| TDW | 2025-07 | LOSS | -0.36 | 0.671 | 0.641 | 1 | True |
| BATRA | 2025-12 | OPEN |  | 0.683 | 0.936 | 2 | False |

Only **two names repeat**, for **three re-entries total**. The strategy has **no outcome memory** — it re-buys whenever a name re-qualifies on fresh insider activity, regardless of how the prior trade ended.

- **BATRA:** Feb LOSS (-0.5%) -> May WIN (+0.9%, marginal) -> Dec OPEN. Its insider score rose 0.75 -> 0.97 by the May re-entry (fresh buying).
- **TDW:** Apr LOSS (-6.3%) -> Jul LOSS (-0.4%). Re-entered after a loss and lost again; its insider score *fell* 0.75 -> 0.64.

Both after-loss re-entries landed near zero (+0.9%, -0.4%). *Hypothesis:* a re-entry-after-loss rule (skip, or size down, a name that just lost) might be worth testing — but **n=2 re-entries proves nothing**; this is an observation to revisit with many years of PIT data, not a signal.

