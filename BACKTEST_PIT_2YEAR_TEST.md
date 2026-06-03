# Task 14 Phase C — PIT 2-Year Regime Test (2022 + 2025)

> ⚠️ **SURVIVOR-BIAS-CORRECTED but still IN-SAMPLE. DO NOT DEPLOY.** Point-in-time IWM N-PORT membership + conservative B3 delisting marks, full 18-config grid (matching Task 12), 3 frozen hypotheses. Headline = pooled Score-Return R² per year. A pass means 'worth forward validation,' not 'deploy.' **2022 is the decisive (trend/down) regime; a 2025-only win = regime artifact.**

## Sensitivity bound (2022 membership coverage)

- 2022 raw membership coverage: **93.9%** (1,874/1,996 N-PORT holdings mapped).
- Residual unmapped: **122** names (de-SPACs / recent IPOs: Cano Health, Nikola, Sovos, Audacy, Hyzon, …).
- **Residual names with 2022 insider buying: 0** (confirmed issuer-side via CIK + owner-name cross-check). Every unmapped name is outside the insider-active scored subset, so **the regime test is unaffected by the residual.**
- Bias-correction magnitude: the 2022 PIT universe **restores 567** wrongly-excluded names and **drops 659** wrongly-included vs the current snapshot (~2/3 of the universe differs).

## Result — pooled Score-Return R² per year (the headline)

| Hyp | Weights | 2022 R² | 2025 R² | 2022 ret(med) | 2025 ret(med) | 2022 Sharpe(med) | 2025 Sharpe(med) |
|---|---|---|---|---|---|---|---|
| H10 | 40/30/20/10/0 | **0.0207** | **0.0001** | -20.3% | 13.6% | -0.39 | 0.65 |
| H13 | 40/0/20/10/30 | **0.0018** | **0.0236** | -76.7% | 9.1% | -1.29 | 0.43 |
| H14 | 50/0/10/10/30 | **0.0091** | **0.0197** | -76.3% | 5.0% | -1.29 | 0.32 |

## Per-year detail (win% and top-3 concentration pooled/median over 18 configs)


**2022**

| Hyp | Trades(med) | Return(med) | Sharpe(med) | Win% | Top3% | R² | DSR(N=3) | Held names that delisted |
|---|---|---|---|---|---|---|---|---|
| H10 | 20 | -20.3% | -0.39 | 38 | -47 | 0.0207 | 0.427 | none |
| H13 | 22 | -76.7% | -1.29 | 29 | -17 | 0.0018 | 0.093 | ENR, FIBK |
| H14 | 22 | -76.3% | -1.29 | 31 | -16 | 0.0091 | 0.174 | ENR, FIBK |

**2025**

| Hyp | Trades(med) | Return(med) | Sharpe(med) | Win% | Top3% | R² | DSR(N=3) | Held names that delisted |
|---|---|---|---|---|---|---|---|---|
| H10 | 23 | 13.6% | 0.65 | 53 | 185 | 0.0001 | 0.911 | none |
| H13 | 24 | 9.1% | 0.43 | 57 | 61 | 0.0236 | 0.832 | none |
| H14 | 23 | 5.0% | 0.32 | 52 | -44 | 0.0197 | 0.838 | none |

## Verdict vs the pre-registered criterion

Baseline H10 pooled R²: 2022 = 0.0207, 2025 = 0.0001.

- **H13** (technical replaces quality): beats H10 in 2022 = **False** (R² 0.0018 vs 0.0207); in 2025 = **True** (R² 0.0236 vs 0.0001) -> BOTH = **False**.

- **H14** (insider+tech dominant (full Baldwin)): beats H10 in 2022 = **False** (R² 0.0091 vs 0.0207); in 2025 = **True** (R² 0.0197 vs 0.0001) -> BOTH = **False**.


### FAIL — At least one of H13/H14 does NOT beat baseline R² in BOTH years. Per the pre-registered criterion, the in-sample Task-12 result was substantially a **regime artifact** once survivor bias is removed. STOP — do not run Phase D. DO NOT DEPLOY.


**2022 (decisive) specifically:** at least one technical hypothesis FAILED to beat baseline in the trend/down regime -> regime artifact.


## Deflated Sharpe note

DSR accounts for the 3 hypotheses (N=3) per year; raw best Sharpes feed it. At T=12 months and 3 trials these are weak either way and **do not tip the decision** — R² consistency across the two regimes is the real read.


## Methodology caveat — regime overlay (FRED unavailable)

FRED (credit-spread / yield-curve inputs) was unreachable during this run, so {2022: 12} rebalance month(s) used a **neutral risk-on regime** (100% equity) instead of the live classification. 2025 used the real FRED-derived regimes cached in Phase 4A. **This does not affect the verdict:** the regime overlay only scales position sizing (risk-off -> 60% equity); it does not change which names are held or their per-trade returns, so the headline Score-Return R² is identical with or without it. Only the secondary return/Sharpe columns for fallback months omit risk-off de-risking (a uniform effect across all three hypotheses, so the comparison still holds).

