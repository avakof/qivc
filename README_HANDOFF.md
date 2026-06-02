# QIVC v2.0 Build Package — Handoff Guide

This package contains everything needed to build the QIVC v2.0 stock-research agent system using Claude Code in VS Code. Three files:

| File | Purpose |
|---|---|
| `PROJECT_BRIEF.md` | Complete architectural specification. Source of truth. Read by Claude Code first. |
| `CLAUDE_PROMPTS.md` | Phase-by-phase prompts to paste into Claude Code. One prompt = one phase = one commit. |
| `README_HANDOFF.md` | This file. Workflow guide for you (the user). |

## Setup (5 minutes)

1. Create an empty git repo on your machine:
   ```bash
   mkdir qivc && cd qivc
   git init
   ```

2. Copy all three `.md` files into the repo root.

3. Open the repo in VS Code. Open the Claude Code panel.

4. Verify Claude Code can see all three files:
   - Type in Claude Code: `ls`
   - You should see `PROJECT_BRIEF.md`, `CLAUDE_PROMPTS.md`, `README_HANDOFF.md`.

## Build Workflow

### Step 1 — Master prompt (once)

Open `CLAUDE_PROMPTS.md` and paste the **"Master Prompt (Paste First, Once)"** block into Claude Code. Wait for Claude Code to confirm it has read `PROJECT_BRIEF.md`.

### Step 2 — Phase 0 through Phase 6 (sequential)

For each phase 0 → 6:

1. Paste that phase's prompt block from `CLAUDE_PROMPTS.md` into Claude Code.
2. Wait for Claude Code to build the phase. This may take 5-30 minutes per phase depending on phase scope.
3. **Critical:** when Claude Code says the phase is done, ask it to run the **acceptance gates** explicitly. Do not move to the next phase until every acceptance gate is PASS.
4. Review the code in VS Code. Spot-check at least 2-3 test cases manually.
5. If anything looks wrong, ask Claude Code to fix it before advancing.
6. Once you're satisfied, paste the next phase's prompt.

### Step 3 — Phase 7 (only when ready)

Phase 7 (backtesting) requires that Phases 0-6 are actually working against live data. **Run `qivc screen` against real EDGAR/yfinance and verify the output makes sense before executing Phase 7.** Backtesting is a research effort, not a build sprint; expect it to surface real issues with the live system.

## What "Done" Looks Like

At end of Phase 6:

```bash
$ uv run qivc regime
Regime: risk-on
VIX 60d SMA: 17.2
Credit spread: 102 bps
Yield curve (10y-3m): +45 bps
Value-Growth 12m: -4.2%
Decision: New entries permitted at full sizing.

$ uv run qivc screen
[2026-05-27 09:00:14] Run abc123 starting
[2026-05-27 09:00:15] Regime: risk-on
[2026-05-27 09:00:18] Ingested 234 Form 4 P-code transactions (last 14 days)
[2026-05-27 09:00:18] Unique tickers: 87
[2026-05-27 09:00:18] Unique insiders: 168
[2026-05-27 09:02:33] Fundamentals fetched for 87 tickers
[2026-05-27 09:03:11] Insider classifications: 142 opportunistic, 18 routine, 8 unclassified
[2026-05-27 09:03:14] Clusters detected: 4 Track A, 2 Track B
[2026-05-27 09:03:18] Candidates surviving all gates: 3
[2026-05-27 09:03:18] Report written: data/runs/abc123/report.md

$ cat data/runs/abc123/report.md
# QIVC v2.0 Screen — 2026-05-27 09:00 UTC
...
```

You then read `report.md` and use it as research input for your own investment decisions. The system does not place trades and does not maintain a portfolio.

## Estimated Time Budget

| Phase | Claude Code time | Your review time | Cumulative |
|---|---|---|---|
| 0 — Scaffold | 30-60 min | 15 min | ~1 hr |
| 1 — Data Layer | 2-4 hr | 30 min | ~5 hr |
| 2 — Filter Layer | 2-3 hr | 30 min | ~8 hr |
| 3 — Orchestration | 2-3 hr | 30 min | ~11 hr |
| 4 — Scoring | 1 hr | 20 min | ~12 hr |
| 5 — CLI & Dossier | 1-2 hr | 30 min | ~14 hr |
| 6 — Tests & CI | 1-2 hr | 30 min | ~16 hr |
| 7 — Backtest | 4-8 hr | 1 hr | ~25 hr |

Phases 0-6 are roughly 2 working days of attended Claude Code use. Phase 7 is a separate research project.

## When to Stop Claude Code

Stop Claude Code immediately if any of these happen:

1. **Scope creep.** Claude Code starts adding features not in `PROJECT_BRIEF.md`. Say: "Stop. Revert to brief. Add only what's specified."
2. **Spec deviation.** Claude Code changes the tech stack or skips a step. Same: revert.
3. **Silent failures.** A phase is "done" but acceptance gates haven't actually been run. Make Claude Code run them and report.
4. **Live API calls in unit tests.** This is a hard rule per the brief. Stop and refactor.

## When to Ask the User (You) for Help

Claude Code is instructed to ask you when:
- The brief is ambiguous (this should be rare; if it happens often, the brief has a bug)
- A live API call is needed during development that requires API keys you haven't provided
- A phase's acceptance gate cannot be met for a documented reason

Respond clearly, then resume.

## Common Issues

**SEC 403 errors:** the User-Agent in `.env` is malformed. Format must be exactly `"Your Name your.email@domain.com"` — no quotes in the value, real email, no placeholder. SEC actively blocks bots without proper UAs.

**yfinance returning None for fundamentals:** yfinance is community-maintained and breaks periodically. The brief's Phase 1 uses edgartools for fundamentals (via XBRL), which is more reliable. yfinance is only for prices, SI, and revisions.

**LangGraph checkpoint errors:** the SqliteSaver requires the parent directory to exist. The Phase 0 scaffold should create `data/duckdb/` directly; verify it exists before running.

**Coverage below 90%:** usually exception handlers or rare edge cases. Don't lower the threshold; write tests for those branches.

## Beyond the Build

After Phase 6 is working, sensible next steps (NOT in the build):

1. Daily cron with email notification on new candidates
2. Web UI (separate project; consume the JSON output)
3. EU MAR Article 19 support (PDMR notifications from CNMV/BaFin/FCA)
4. Alternative data integrations (patent filings, hiring trends)
5. Machine learning conviction weighting (only after several years of live data)

These are separate projects with their own briefs.

---

End of handoff guide. Start with the Master Prompt in `CLAUDE_PROMPTS.md`.
