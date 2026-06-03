# Task 12 — Technical-Oversold Factor: 5 Pre-Registered Weight Hypotheses

> ⚠️ **IN-SAMPLE, SURVIVOR-BIASED 2025, DO NOT DEPLOY.** This tests EXACTLY 5 FINAL weight hypotheses (no sweeps, no tuning) across the same 18 structural configs from Phase 4A. The headline is **Score-Return R²** — does any weighting make the composite predict returns? Baseline (Task 11) R² = 0.004. The highest-return hypothesis is **NOT** 'the answer'; it is at most the candidate most worth validating on clean multi-year PIT data. One year, one regime, 90 config-hypothesis cells — statistically void.

## Comparison table (aggregated across the 18 configs per hypothesis)

Weights are insider/quality/valuation/momentum/technical (%). Return, Sharpe, Trades, Top3 are the **median across the 18 configs**; Win% and R² are **pooled across all trades in all 18 configs**.

| Hyp | Weights | Desc | Med Trades | Med Return% | Med Sharpe | Best Sharpe | DSR(N=90) | DSR(N=5) | Win% | Med Top3% | Pooled R² | n trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H10 | 40/30/20/10/0 | v3.0 baseline (control, no technical) | 20 | 37.5 | 1.14 | 2.02 | 0.981 | 0.997 | 60 | 71 | **0.0040** | 287 |
| H2 | 70/10/10/10/0 | insider-dominant (quality diluted) | 20 | 31.8 | 1.01 | 2.16 | 0.989 | 0.999 | 62 | 71 | **0.0351** | 263 |
| H13 | 40/0/20/10/30 | technical replaces quality (takes its 30%) | 20 | 49.6 | 1.23 | 1.70 | 0.900 | 0.972 | 71 | 70 | **0.0415** | 286 |
| H14 | 50/0/10/10/30 | insider+tech dominant (full Baldwin, drop quality) | 20 | 43.9 | 1.15 | 1.75 | 0.917 | 0.968 | 69 | 70 | **0.0452** | 276 |
| H15 | 35/15/20/10/20 | balanced w/ technical (supplements quality) | 20 | 26.5 | 0.82 | 1.46 | 0.852 | 0.961 | 59 | 97 | **0.0164** | 289 |

## Per-config R² robustness (is any improvement structural or one lucky config?)

For each hypothesis: median and max R² across its 18 configs, and how many of 18 configs clear R² > 0.01 (a very low bar). If a hypothesis only shows high pooled R² because of one config, `# cfgs R²>0.01` will be small.

| Hyp | Pooled R² | Median per-cfg R² | Max per-cfg R² | # cfgs R²>0.01 (of 18) |
|---|---|---|---|---|
| H10 | 0.0040 | 0.0087 | 0.1566 | 9 |
| H2 | 0.0351 | 0.0338 | 0.1926 | 12 |
| H13 | 0.0415 | 0.0791 | 0.7961 | 18 |
| H14 | 0.0452 | 0.0918 | 0.7643 | 18 |
| H15 | 0.0164 | 0.0521 | 0.3808 | 15 |

## Return vs R² (the pattern to watch)

Per the Phase-1 note: a technical hypothesis with **lower return but higher R²** would suggest it is trading survivor-luck (e.g. avoiding names like CELC that were near their highs when held) for genuine ranking signal — the thing we want to detect. Flagged below where it occurs.

- **H2**: ΔReturn vs H10 = -5.7pp, ΔR² = +0.0311 ⬅ **LOWER RETURN, HIGHER R²**
- **H13**: ΔReturn vs H10 = +12.0pp, ΔR² = +0.0374
- **H14**: ΔReturn vs H10 = +6.3pp, ΔR² = +0.0412
- **H15**: ΔReturn vs H10 = -11.0pp, ΔR² = +0.0123 ⬅ **LOWER RETURN, HIGHER R²**

## Deflated Sharpe — trial counting (explicit)

- **DSR(N=90)** deflates each hypothesis's best-config Sharpe against the full set of **90 config-hypothesis cells** actually evaluated. This is the strict, honest bar: the whole program is one big search.
- **DSR(N=5)** deflates against just the 5 hypotheses' best Sharpes — the lenient view that treats each hypothesis as a single pre-registered trial.
- **With one year of monthly data (T=12) and 90 cells, even DSR(N=90) is weak evidence either way.** A high DSR here is necessary, not sufficient; a low one is a clear warning. Do not over-read either.


## Honest interpretation (required 6 questions)

**1. Does technical-oversold improve R² over baseline's 0.0040? Yes — and unlike anything in this project so far, structurally.** Every hypothesis that down-weights quality lifts pooled R² well above 0.004: H2=0.0351, H13=0.0415, H14=0.0452, H15=0.0164. The technical-heavy H13/H14 clear R²>0.01 in **18/18 and 18/18 configs** (median per-config R² 0.079/0.092), so the lift is NOT one lucky config — it holds across the whole structural grid. **Caveat that bounds the whole result:** pooled R²≈0.045 still leaves ~95% of return variance unexplained, and it is in-sample on 2025, a small-cap mean-reversion year. An oversold factor *mechanically* correlates with returns in a bounce year; this may be a regime artifact, not a durable edge.

**2. Is technical better than quality? Clearly, in-sample.** H13 (technical takes quality's exact 30%) vs H10: ΔR² = +0.0374 (~10x), ΔReturn = +12.0pp. But the sharper evidence is H2: dropping quality's weight to 10% **with no technical at all** already lifts R² to 0.0351. So most of the gain is from *removing quality* (which the Phase-4A winner/loser table flagged as higher in losers), and technical adds a further increment on top. Quality, as built (Piotroski + GP/A), was actively diluting the ranking here.

**3. Replace or supplement quality? Replace.** H13 (replace, R²=0.0415, 18/18 configs) beats H15 (supplement 35/15/20/10/20, R²=0.0164, 15/18). Keeping 15% quality (H15) *lowers* both R² and return (26.5% vs 49.6%) vs dropping it entirely — consistent with quality being a drag, not a help, in this universe.

**4. Does the full-Baldwin bet (H14, 50/0/10/10/30) win or lose? It posts the highest R² (0.0452, 18/18 configs)** and a median return of 43.9%. On the headline metric H14 is the leader, narrowly over H13. DSR(N=90)=0.917 — high, but see Q on DSR: with T=12 and 90 cells these DSRs are near-uninformative and should not tip the decision.

**5. Does H2 (70/10/10/10/0, no technical) reconfirm the insider-dominant finding? Yes.** R²=0.0351 vs baseline 0.0040 (~9x) with ΔReturn -5.7pp — the **lower-return / higher-R² pattern** flagged in Phase 1. Down-weighting quality improves *ranking* while giving up some in-sample return (it stops over-weighting the high-quality names that happened to be 2025 losers). This independently reconfirms 'quality dilutes the signal' WITHOUT the new factor — the technical result is not the only thing pointing at quality.

**On the return-vs-R² pattern you asked me to flag:** H2 and H15 show the *lower-return/higher-R²* trade (giving up survivor-luck return for ranking signal). H13 and H14, however, improved **both** return and R². That is NOT independent confirmation — in a 2025 mean-reversion tape, an oversold factor both predicts returns (higher R²) and rides the bounce (higher return) by the same mechanism. So read H13/H14's extra return as the same regime effect that produces their R², not as a second, separate point in their favour.

**6. Which single hypothesis is most worth PIT validation — and is the margin worth it?** **H14** (and H13 close behind): R²=0.0452 vs 0.0040, structural across 18/18 configs. This is the first time in the project that *any* ranking has correlated with forward returns beyond noise — so the honest answer is **not** 'pivot': the margin (~10x baseline R², structural, with a clear mechanism — drop quality, add oversold) is large and consistent enough to **earn one clean PIT validation**. The pre-registered question for that test is narrow: *does the drop-quality + technical-oversold ranking (H13/H14) still beat baseline R² on survivor-bias-free, multi-year data, or was it a 2025 mean-reversion artifact?* Bounds on the enthusiasm: absolute R² is still ~0.045 (95% unexplained); hold-180 configs (tiny n) inflate the per-config max; one year is one regime. **DO NOT DEPLOY any of these — H14 winning in-sample is a hypothesis that earned a test, not a model to trade. Paper-trading continues on v3.0 baseline.**

