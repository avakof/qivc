# QIVC v2.0 Agent System — Project Brief

> **Read first.** This document is the single source of truth for the build. Do not invent requirements not specified here. If you find a contradiction or ambiguity, stop and ask for clarification before coding.

---

## 0. How to Use This Document

This brief defines a 7-phase build. Each phase is **independently shippable** with hard acceptance criteria. Execute phases sequentially. Stop at the end of each phase, run the phase's acceptance tests, and report status before moving on.


**Do not skip phases. Do not write tests last. Do not "improve" the spec. If the spec is wrong, say so; do not silently deviate.**

---

## 1. Project Context

### 1.1 What QIVC v2.0 Is

A long-only equity research system that surfaces stock candidates passing all of:

1. **Quality:** Piotroski F-Score ≥ 7 AND Gross-Profitability-to-Assets (GP/A) ≥ industry median
2. **Valuation:** sector-appropriate metric at-or-below a sector-aware threshold
3. **Forward signal:** 90-day analyst EPS revisions non-negative
4. **Insider conviction:** Track A (cluster of ≥3 opportunistic open-market buyers within 7 days) OR Track B (1 C-suite opportunistic buyer with size threshold), where opportunistic is per Cohen-Malloy-Pomorski (2012) — an insider is *routine* if they trade in the same calendar month for ≥3 consecutive prior years; everyone else is opportunistic
5. **Liquidity:** market cap ≥ $300M, ADV ≥ 20× intended position size
6. **No event:** no earnings within next 5 trading days, no pending M&A
7. **Short-interest direction:** passes a 4-case logic matrix
8. **Regime:** current market regime permits new entries

### 1.2 What This Build Is

A Python package + CLI that:
- Ingests SEC EDGAR Form 4, 10-K, 10-Q data
- Computes all filter signals from raw filings
- Classifies insiders as opportunistic/routine
- Surfaces candidate names with full attribution per gate
- Produces a structured dossier output (JSON + human-readable Markdown)
- Logs every filter decision for audit
- Supports both live screening and a backtest harness (Phase 7)

### 1.3 What This Build Is NOT

- **Not a trading bot.** No order placement, no broker integration, no execution logic. Output is research material consumed by a human.
- **Not investment advice.** UI, logs, and reports must consistently say "research candidates," not "recommendations." Output must be jurisdictionally neutral (no buy/sell language).
- **Not a portfolio manager.** Position sizing logic is exposed for the user to apply themselves; the system does not track holdings, P&L, or executed trades.
- **Not real-time.** Form 4 has a T+2 SEC filing window. The system polls daily, not by the second.

### 1.4 Regulatory Posture

The user is in Madrid (EU jurisdiction). Output is research output, not personalized recommendation. Every UI surface and report header must include the disclaimer:

> "This is research output, not investment advice. No personal recommendation is intended. Verify all signals independently before any investment decision."

---

## 2. Tech Stack (Pinned)

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Modern type hints, performance |
| Package manager | `uv` | 10-100× faster than poetry, drop-in compatible |
| SEC EDGAR access | `edgartools` (dgunning/edgartools, MIT) | Free, active, parses Form 4 + XBRL fundamentals natively |
| Market data (prices, SI, calendar) | `yfinance` (primary) + optional FMP API key (fallback) | Free tier sufficient; paid only if hit rate limits |
| Orchestration | `langgraph` 1.0+ | Checkpointing for resumable runs, time-travel debug |
| Data storage | `duckdb` | Single-file, fast, SQL, embeds in process |
| Schemas | `pydantic` v2 | Type-safe contracts between agents |
| CLI | `typer` | Modern, type-driven |
| Testing | `pytest` + `pytest-asyncio` + `responses` | HTTP mocking for deterministic tests |
| Backtest harness | `vectorbt` | Fastest, supports custom signals |
| Lint | `ruff` (lint + format) | Single tool, fast |
| Type check | `mypy` strict | Catch contract violations |
| HTTP | `httpx` (async) | Modern async client, drop-in for `requests` |
| Logging | `structlog` | Structured JSON logs, audit-friendly |

**Do not introduce dependencies outside this list without asking.** Specifically: no LangChain, no CrewAI, no PydanticAI, no pandas-ta. Keep the surface small.

---

## 3. Architecture Overview

### 3.1 Logical Flow

```
                    ┌─────────────────────┐
                    │  daily_screen.py    │  ← CLI entry point
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   LangGraph DAG     │
                    │  (orchestrator)     │
                    └──────────┬──────────┘
                               │
        ┌──────────────────────┼──────────────────────────┐
        ▼                      ▼                          ▼
   DATA LAYER             FILTER LAYER                SYNTHESIS LAYER
   (parallel)             (sequential gates)          (sequential)

   form4_agent            fscore_filter               conviction_scorer
   insider_history_agent  gpa_filter                  risk_overlay
   fundamentals_agent     valuation_filter            dossier_builder
   valuation_agent        revisions_filter
   short_interest_agent   insider_classifier          ┌──────────┐
   estimates_agent        cluster_detector            │ Storage  │
   liquidity_agent        liquidity_filter            │ (DuckDB) │
   regime_agent           short_interest_filter       │  + JSON  │
                                                      └──────────┘
```

### 3.2 Data Flow Phases

1. **Ingest:** Pull last 14 days of Form 4 P-code transactions → DuckDB
2. **Enrich:** For each ticker that appears, fetch fundamentals, valuation, SI, revisions, liquidity
3. **Classify:** For each insider on each transaction, classify as opportunistic/routine using 3-year history
4. **Filter:** Apply 10 gates sequentially; record which gate (if any) kills each candidate
5. **Score:** For survivors, compute 4-tier conviction score → indicative size
6. **Overlay:** Apply current regime cap, drawdown rules, sector caps
7. **Emit:** Generate dossier (Markdown + JSON) with full attribution

### 3.3 Key Design Decisions

- **Pull, don't subscribe.** SEC EDGAR is polled on schedule. No webhook/streaming complexity.
- **Bulk for historical, API for fresh.** Use sec.gov/files bulk dumps for 3-year insider history (one-time + monthly refresh). Use EDGAR API only for the rolling 14-day window.
- **Cache aggressively.** Insider classifications are stable (TTL 90 days). Sector medians refresh weekly. Fundamentals refresh on filing date.
- **Idempotent reruns.** Running the screen twice on the same day produces byte-identical output (apart from timestamps).
- **Explicit "unverifiable" state.** When a gate cannot be evaluated (missing data), the candidate is marked `STATUS: UNVERIFIABLE` with the specific reason. It is never silently passed or dropped.

---

## 4. Project Structure

```
qivc/
├── pyproject.toml            # uv-managed deps, ruff/mypy config
├── uv.lock
├── .env.example              # template; .env is gitignored
├── .gitignore
├── README.md                 # build instructions, usage
├── PROJECT_BRIEF.md          # this file
│
├── src/qivc/
│   ├── __init__.py
│   ├── config.py             # Pydantic Settings; loads .env
│   ├── schemas.py            # All Pydantic models (Transaction, Candidate, Dossier, …)
│   ├── exceptions.py         # QivcError hierarchy
│   ├── logging_config.py     # structlog setup
│   │
│   ├── data/                 # Data-layer agents
│   │   ├── __init__.py
│   │   ├── edgar_client.py   # Rate-limited EDGAR wrapper around edgartools
│   │   ├── form4_agent.py
│   │   ├── insider_history_agent.py
│   │   ├── fundamentals_agent.py
│   │   ├── valuation_agent.py
│   │   ├── short_interest_agent.py
│   │   ├── estimates_agent.py
│   │   ├── liquidity_agent.py
│   │   └── regime_agent.py
│   │
│   ├── filters/              # Filter-layer logic (pure functions, no I/O)
│   │   ├── __init__.py
│   │   ├── fscore.py
│   │   ├── gpa.py
│   │   ├── valuation.py
│   │   ├── revisions.py
│   │   ├── insider_classifier.py    # Cohen-Malloy-Pomorski
│   │   ├── cluster_detector.py
│   │   ├── liquidity.py
│   │   ├── short_interest.py
│   │   └── regime.py
│   │
│   ├── synthesis/
│   │   ├── __init__.py
│   │   ├── conviction.py     # 4-tier scoring
│   │   ├── risk_overlay.py   # regime cap, sector cap, drawdown
│   │   └── dossier.py        # Build JSON + Markdown output
│   │
│   ├── orchestration/
│   │   ├── __init__.py
│   │   ├── graph.py          # LangGraph StateGraph definition
│   │   ├── state.py          # Shared state schema
│   │   └── nodes.py          # Node functions (wrap agents/filters)
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py             # DuckDB connection, migrations
│   │   └── repositories.py   # CRUD by domain (transactions, candidates, runs)
│   │
│   ├── cli/
│   │   ├── __init__.py
│   │   └── main.py           # Typer app: `qivc screen`, `qivc backtest`, `qivc audit`
│   │
│   └── backtest/             # Phase 7
│       ├── __init__.py
│       └── harness.py        # vectorbt integration
│
├── tests/
│   ├── conftest.py
│   ├── fixtures/             # Recorded Form 4 XML, 10-K samples
│   │   ├── form4_unh_apr2026.json
│   │   ├── form4_fcn_may2026.json
│   │   ├── tenk_fcn_2025.xml
│   │   └── ...
│   ├── unit/
│   │   ├── filters/
│   │   ├── data/
│   │   └── synthesis/
│   ├── integration/
│   │   └── test_end_to_end.py    # Full pipeline on fixture data
│   └── live/
│       └── test_live_smoke.py    # Marked @live, skipped by default
│
├── data/                     # Local data dir (gitignored)
│   ├── duckdb/qivc.db
│   ├── bulk/                 # SEC bulk dumps
│   └── cache/
│
└── scripts/
    ├── bootstrap_bulk_data.py    # One-time: download SEC bulk dumps
    ├── refresh_sector_medians.py # Weekly cron
    └── reset_db.py
```

---

## 5. Phased Build Plan

Each phase produces a vertical slice that can be tested end-to-end. Acceptance criteria are non-negotiable; if any criterion fails, that phase is not done.

### Phase 0 — Scaffold (target: 1-2 hours)
**Goal:** Empty project with tooling configured. No business logic.

**Deliverables:**
- `pyproject.toml` with all pinned deps
- `uv.lock` committed
- `.env.example` with documented variables
- `.gitignore` (Python, venv, IDE, `.env`, `data/`)
- `src/qivc/__init__.py` with `__version__ = "0.1.0"`
- `src/qivc/config.py` with `Settings` class (BaseSettings, env-driven)
- `src/qivc/logging_config.py` with structlog setup
- `src/qivc/cli/main.py` with `qivc --version` working
- `ruff` + `mypy` + `pytest` configs in `pyproject.toml`
- `tests/conftest.py` with empty fixture scaffolding
- A passing smoke test: `pytest -q` exits 0

**Acceptance criteria:**
1. `uv sync` succeeds on a clean checkout
2. `uv run qivc --version` prints `0.1.0`
3. `uv run pytest -q` passes (zero tests is acceptable; failures are not)
4. `uv run ruff check src tests` passes
5. `uv run mypy src` passes (strict mode)

### Phase 1 — Data Layer (target: 1 day)
**Goal:** Each data agent works in isolation against fixtures, with a clean async interface.

**Deliverables:**
- `EdgarClient` wrapper with rate-limit guard (8 req/sec, exponential backoff on 429/403)
- All 8 data agents implemented as classes with single `async def fetch(...) -> <Model>` method
- Pydantic schemas in `schemas.py` for all returned objects
- Test fixtures: at least 3 real Form 4 filings (UNH cluster, FCN cluster, FTI), 1 10-K XBRL, 1 10-Q
- Unit tests for every agent against fixtures, 100% method coverage on agent classes

**Acceptance criteria:**
1. `pytest tests/unit/data -q` passes, ≥ 95% coverage on `src/qivc/data/`
2. `EdgarClient` enforces rate limit (test injects fake clock, verifies sleep)
3. User-Agent header is set per SEC requirement (`Settings.edgar_user_agent`)
4. No live HTTP calls in unit tests (all mocked with `responses`)
5. `mypy src/qivc/data` passes strict

### Phase 2 — Filter Layer (target: 1 day)
**Goal:** Pure-function filters that take typed inputs and return typed `FilterResult` objects.

**Each filter has the signature:** `def apply(candidate: CandidateInput, **filter_specific) -> FilterResult`

`FilterResult` is `{passed: bool, reason: str, metric_value: float | None, threshold: float | None}`.

**Critical filter:** the insider classifier. Implement Cohen-Malloy-Pomorski exactly:
- Input: list of historical transactions for a single insider, plus the candidate transaction
- Logic: examine the prior 3 years of history. If the insider traded in the same calendar month in each of 3 consecutive prior years, the candidate trade is **routine**. Otherwise **opportunistic**.
- Edge case: insider with < 3 years of history → mark as `UNCLASSIFIED`, not opportunistic (do not assume).

**Deliverables:**
- All 10 filters in `src/qivc/filters/` as pure functions
- Hand-computed test cases for each filter (especially F-Score: full 9-component example)
- Property-based tests with `hypothesis` for boundary conditions on numeric filters

**Acceptance criteria:**
1. `pytest tests/unit/filters -q` passes, ≥ 98% coverage on `src/qivc/filters/`
2. F-Score test includes a known-good example (build one from a real 10-K in fixtures)
3. CMP classifier test: insider with 3 prior Mays → May trade is routine; insider with 2 prior Mays → opportunistic
4. No imports from `src/qivc/data/` in `src/qivc/filters/` (filters are pure, enforced by import-lint test)

### Phase 3 — Orchestration (target: 1 day)
**Goal:** LangGraph DAG that runs the full pipeline on a list of candidate tickers.

**Deliverables:**
- `State` TypedDict / Pydantic model in `orchestration/state.py`
- LangGraph `StateGraph` in `orchestration/graph.py` wiring data agents → filters → synthesis
- Node functions in `orchestration/nodes.py` that adapt agents/filters to the graph
- Checkpointing configured: SQLite checkpointer for resumability (no Postgres dep)
- Run audit table in DuckDB: every node entry/exit, duration, result

**Acceptance criteria:**
1. End-to-end test: feed a list of 3 fixture tickers, get back a list of dossiers
2. Killing the process mid-run and restarting resumes from the last checkpoint
3. Run audit log shows every node executed for every ticker, with timing
4. Graph is visualizable: `qivc graph --output graph.png` works

### Phase 4 — Scoring & Risk Overlay (target: half day)
**Goal:** Convert filter pass/fail outcomes into a ranked candidate list with indicative sizing.

**Deliverables:**
- `conviction_scorer.py`: implements 4-dimension scoring per spec section B
- `risk_overlay.py`: applies regime cap (looks up current regime from RegimeAgent), sector caps, drawdown adjustment placeholders (drawdown requires portfolio state which we don't track — emit "applicable cap = X%" but do not apply it absent state)
- Score → size mapping: 0-3 → 3%, 4-6 → 5%, 7-9 → 7%, 10+ → 10%

**Acceptance criteria:**
1. Unit tests cover all score boundaries (3/4, 6/7, 9/10)
2. Sector cap test: 3 candidates in same GICS sector → 3rd is flagged "EXCEEDS_SECTOR_CAP"
3. Regime cap test: when regime is risk-mid, indicative sizing is halved; risk-off, third'd

### Phase 5 — CLI & Dossier Output (target: half day)
**Goal:** A user can run `qivc screen` and get a Markdown report + JSON file.

**Commands:**
- `qivc screen` — full pipeline, output to `data/runs/<timestamp>/`
- `qivc screen --ticker UNH` — single-ticker mode (no Form 4 ingest, just runs all gates on the ticker)
- `qivc audit <run_id>` — show why a specific ticker passed/failed each gate
- `qivc regime` — just print the current regime classification

**Output format (Markdown, one file per run):**

```markdown
# QIVC v2.0 Screen — {YYYY-MM-DD HH:MM UTC}

**Regime:** Risk-On (VIX 60d SMA: 16.4, Credit spread: …)
**Candidates surviving all gates:** N
**Candidates rejected:** M (see appendix)

---

## Candidate 1: {TICKER} — {Company Name}

| Gate | Result | Detail |
|---|---|---|
| F-Score ≥ 7 | PASS (8) | ROA +, OCF +, ΔROA +, … |
| GP/A ≥ industry median | PASS (0.42 vs. 0.31) | … |
| ... | ... | ... |

**Cluster:** 3 opportunistic insiders, May 13 2026, $2.08M aggregate
**Indicative sizing:** 5% (conviction score 6/12)
**Regime cap:** 100% (risk-on, no reduction)

---

## Appendix: Rejected Candidates

| Ticker | Failed Gate | Reason |
|---|---|---|
| ... | ... | ... |
```

JSON output: machine-readable mirror of the Markdown, schema in `schemas.py:RunReport`.

**Acceptance criteria:**
1. `qivc screen --ticker UNH` produces a Markdown file with all 10 gate results
2. Disclaimer text appears at top of every Markdown output
3. JSON output validates against `RunReport` schema
4. `qivc audit <run_id>` correctly retrieves prior run data from DuckDB

### Phase 6 — Tests & CI (target: half day)
**Goal:** Production-ready CI pipeline.

**Deliverables:**
- `.github/workflows/ci.yml`: ruff, mypy, pytest, coverage gate (≥ 90%)
- Pre-commit hooks for ruff format + check
- Integration test that runs the full pipeline against fixtures
- A "live smoke" test (skipped by default, runnable manually with `pytest -m live`) that hits real EDGAR once to verify rate-limit guard and User-Agent

**Acceptance criteria:**
1. CI runs green on first commit
2. Coverage report shows ≥ 90% on `src/qivc/`
3. `pre-commit run --all-files` passes

### Phase 7 — Backtesting Harness (deferred but specified; target: 1-2 days)
**Goal:** A separate command `qivc backtest` that, given a date range, replays the screen historically and reports simulated returns.

**Constraints:**
- Use only point-in-time data (no look-ahead). Specifically, F-Score at decision date uses only filings published before that date; sector medians use only constituents known then.
- Realistic transaction costs: 5 bps slippage + commission (configurable).
- Survivor bias correction: backtest universe must include de-listed tickers. Use CRSP if user provides; otherwise document as limitation.
- Output: equity curve, max drawdown, Sharpe, hit rate, names held, factor attribution vs. Russell 2000 Value.

**Note:** Phase 7 is documented here for completeness but executed only after Phases 0-6 ship and the user confirms.

---

## 6. Agent Interface Contracts

Every data agent implements this interface:

```python
from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class DataAgent(ABC, Generic[T]):
    @abstractmethod
    async def fetch(self, **kwargs) -> T:
        """Fetch and return typed data. Raises QivcDataError on failure."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Stable identifier for audit logging."""
```

Every filter is a pure function:

```python
def apply(candidate: CandidateInput, **params) -> FilterResult:
    ...
```

`FilterResult`:

```python
class FilterResult(BaseModel):
    filter_name: str
    passed: bool | None  # None = UNVERIFIABLE
    metric_value: float | None
    threshold: float | None
    reason: str  # Human-readable explanation
```

---

## 7. Data Source Configuration

### 7.1 SEC EDGAR (via edgartools)

```python
# config.py
edgar_user_agent: str = Field(..., description="Format: 'CompanyName email@domain.com'")
edgar_rate_limit_rps: int = 8  # Below 10 req/sec SEC limit for safety
```

On startup, call `edgar.set_identity(settings.edgar_user_agent)`.

### 7.2 Market Data (yfinance + optional FMP)

```python
fmp_api_key: str | None = None  # Optional; falls back to yfinance
yfinance_retry_count: int = 3
```

`ValuationAgent`, `ShortInterestAgent`, `LiquidityAgent` all use yfinance by default. Sector median computation uses sector ETF holdings (XLF, XLY, XLI, etc.) refreshed weekly via `scripts/refresh_sector_medians.py`.

### 7.3 Regime Indicators

`RegimeAgent` pulls from FRED (free, no API key required for series download):
- VIXCLS (VIX daily close)
- BAA10Y minus AAA10Y (credit spread)
- T10Y3M (yield curve)
- Computes value-vs-growth 12m via yfinance: IVE total return − IVW total return

Regime classification:
- Risk-on (default): VIX 60d SMA < 25 AND credit spread not widening AND yield curve not deeply inverted
- Risk-mid: VIX 60d SMA between 25 and 35 OR credit spread expanded >50bps in 60d
- Risk-off: VIX 60d SMA > 35 OR yield curve inverted AND earnings revisions falling

---

## 8. Testing Strategy

### 8.1 Unit Tests
- Every filter: hand-computed cases, especially boundaries
- Every data agent: mocked HTTP, verifies parsing and rate-limit behavior
- Every synthesis function: input/output table

### 8.2 Integration Tests
- One end-to-end test: pipeline runs on 3 fixture tickers, produces expected Markdown report (golden file)
- LangGraph checkpointing: kill mid-run, restart, verify resumption

### 8.3 Live Smoke Tests (manual)
- `pytest -m live` runs real EDGAR + yfinance calls, verifies:
  - Rate limit guard never trips a 403
  - User-Agent present in actual outgoing headers
  - At least one real Form 4 returns parseable data
- Skipped in CI by default

### 8.4 Property-Based Tests (hypothesis)
- F-Score: for any combination of valid component values, score is in [0, 9]
- Conviction scorer: monotonic in each input dimension
- Cluster detector: idempotent under transaction re-ordering within the 7-day window

### 8.5 Coverage Gate
- 90% overall on `src/qivc/`
- 98% on `src/qivc/filters/` (these are the highest-stakes pure functions)

---

## 9. Configuration

### 9.1 `.env.example`

```bash
# Required
QIVC_EDGAR_USER_AGENT="YourName your.email@example.com"

# Optional
QIVC_FMP_API_KEY=
QIVC_LOG_LEVEL=INFO
QIVC_LOG_FORMAT=json
QIVC_DB_PATH=data/duckdb/qivc.db
QIVC_OUTPUT_DIR=data/runs

# Tuning (defaults shown)
QIVC_EDGAR_RATE_LIMIT_RPS=8
QIVC_FORM4_LOOKBACK_DAYS=14
QIVC_CMP_HISTORY_YEARS=3
QIVC_CLUSTER_WINDOW_DAYS=7
QIVC_MIN_MARKET_CAP_USD=300000000
```

### 9.2 Sector Mappings

Hard-coded mapping in `src/qivc/filters/valuation.py`:

```python
SECTOR_VALUATION_METRIC = {
    # GICS Sector → metric
    "Information Technology": "forward_pe",
    "Communication Services": "forward_pe",
    "Consumer Discretionary": "forward_pe",
    "Consumer Staples": "forward_pe",
    "Health Care": "forward_pe",  # except specific sub-industries
    "Industrials": "ev_ebitda",
    "Materials": "ev_ebitda",
    "Energy": "ev_ebitda",
    "Utilities": "ev_ebitda",
    "Financials": "p_tbv",
    "Real Estate": "p_affo",
}

SECTOR_VALUATION_PREMIUM = {
    # Sector → allowed premium to median (1.0 = at median, 1.1 = 10% premium allowed)
    "Information Technology": 1.10,
    "Communication Services": 1.10,
    "Consumer Discretionary": 1.10,
    "Consumer Staples": 1.10,
    "Health Care": 1.10,
    "Industrials": 1.00,
    "Materials": 1.00,
    "Energy": 1.00,
    "Utilities": 1.00,
    "Financials": 1.10,
    "Real Estate": 1.00,
}
```

---

## 10. Runbook (Post-Build)

```bash
# One-time setup
git clone <repo>
cd qivc
uv sync
cp .env.example .env
# edit .env with your EDGAR user agent

# One-time bootstrap of historical insider data (used for CMP classification)
uv run python scripts/bootstrap_bulk_data.py
# Takes 30-60 minutes; downloads SEC bulk Form 4 dumps for prior 3 years

# Daily run
uv run qivc screen

# Check current regime
uv run qivc regime

# Single-ticker investigation
uv run qivc screen --ticker UNH

# Audit a past run
uv run qivc audit <run_id>

# Weekly maintenance
uv run python scripts/refresh_sector_medians.py
```

### Scheduling

Daily run via cron at 9:00 ET (post-market-open, after overnight Form 4 filings settled):

```cron
0 9 * * 1-5 cd /path/to/qivc && uv run qivc screen >> data/runs/cron.log 2>&1
```

---

## 11. Known Limitations & Future Work

| Limitation | Mitigation |
|---|---|
| Short interest is bi-monthly (FINRA) | Document staleness in dossier output |
| Sector medians use ETF holdings, not full GICS membership | Adequate for screening; backtests may need CRSP/Compustat |
| No de-listed tickers in live screen | Acceptable; live screen surfaces live names by definition |
| Insider classification needs 3 years history | Insiders with < 3 years → `UNCLASSIFIED`, conservatively excluded from opportunistic count |
| No real-time alerts | Out of scope; user runs daily |
| EU MAR Article 19 support | Out of scope for initial build; data layer interface designed to permit later addition |
| Backtest universe | Phase 7 only; explicitly documented as separate effort |

---

## 12. Acceptance Definition for the Whole System

The build is done when:

1. `qivc screen` runs end-to-end against live EDGAR + yfinance without errors
2. Output Markdown file is human-readable and contains all required sections
3. JSON output validates against schema
4. CI is green
5. Coverage is ≥ 90%
6. `pytest -m live` (run manually) verifies no rate-limit violations against real APIs
7. README documents the runbook
8. A naive user can clone the repo, follow the README, and produce their first screen within 30 minutes (excluding bootstrap_bulk_data time)

---

## 13. Things to Ask the User About If Unclear

Before coding, ask if any of these are ambiguous to you:

- Should the system support paper trading integration? (Default: NO, out of scope)
- Should the conviction scoring include the insider track record sub-dimension (5y backtest of their prior buys)? (Default: YES, but implement with `UNCLASSIFIED` fallback when history is sparse)
- Should the regime overlay actually block entries, or just annotate them? (Default: BLOCK in risk-off, ANNOTATE in risk-mid)
- Should EU-jurisdiction PDMR data be in the initial build? (Default: NO, US Form 4 only; architecture preserves clean abstraction)
- Should there be a web UI? (Default: NO, CLI + Markdown files only)

If the answer to any of the above is yes, this brief needs an explicit update before that work is undertaken.

---

