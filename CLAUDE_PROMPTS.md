# QIVC v2.0 — Claude Code Phase Prompts

> **How to use this file:** for each phase below, copy the prompt verbatim into Claude Code (in VS Code). Each prompt is self-contained and assumes `PROJECT_BRIEF.md` is in the workspace root. **Do not run multiple phases in one turn.** Wait for Claude Code to complete a phase, run the acceptance tests, then move to the next.

---

## Master Prompt (Paste First, Once)

```
You are building a Python financial-research system called QIVC v2.0.
The complete specification is in PROJECT_BRIEF.md at the repo root.

Operating rules:
1. Read PROJECT_BRIEF.md in full before writing any code.
2. The brief is the source of truth. Do not invent requirements, do not
   change the tech stack, do not skip the testing strategy.
3. You will build in 7 phases. I will tell you when to start each phase.
   Do not advance phases without my explicit instruction.
4. After each phase, run the acceptance criteria for that phase and
   report PASS/FAIL on each. Do not declare a phase done if any
   criterion fails.
5. If you find an ambiguity, contradiction, or missing detail in the
   brief, stop and ask. Do not paper over it.
6. Use uv for all package operations. Do not use pip, poetry, or conda.
7. Every file you create must pass ruff + mypy strict on commit.
8. Tests are written FIRST against fixtures, then implementation
   passes them. Not the other way around.
9. Commit at the end of each phase with message format:
   "phase{N}: {short description}"

Confirm you have read PROJECT_BRIEF.md and are ready for Phase 0.
```

---

## Phase 0 Prompt — Scaffold

```
Execute Phase 0 (Scaffold) from PROJECT_BRIEF.md.

Goal: empty project, all tooling configured, no business logic.

Specifically:
1. Initialize a uv project: `uv init --package qivc`
2. Add these dependencies (latest stable as of May 2026):
   - Runtime: pydantic>=2.7, pydantic-settings, typer, structlog,
     httpx, duckdb, edgartools, yfinance, langgraph>=1.0,
     langgraph-checkpoint-sqlite
   - Dev: pytest, pytest-asyncio, pytest-cov, responses, hypothesis,
     ruff, mypy
3. Create the directory structure exactly as specified in section 4
   of PROJECT_BRIEF.md. Empty __init__.py files in every package.
4. Implement:
   - src/qivc/__init__.py with __version__ = "0.1.0"
   - src/qivc/config.py with a Settings(BaseSettings) class that
     loads every env var listed in section 9.1 of the brief
   - src/qivc/logging_config.py with a configure_logging() function
     using structlog (JSON output when QIVC_LOG_FORMAT=json)
   - src/qivc/cli/main.py with a Typer app exposing `qivc --version`
5. Configure pyproject.toml:
   - [tool.ruff] with line-length=100, select=["E","F","I","UP","B","SIM","RUF"]
   - [tool.mypy] with strict=true, plugins=["pydantic.mypy"]
   - [tool.pytest.ini_options] with asyncio_mode="auto", testpaths=["tests"]
   - [project.scripts] qivc = "qivc.cli.main:app"
6. Create .env.example matching section 9.1 verbatim
7. Create a comprehensive .gitignore (Python, IDE, venv, .env,
   data/, *.duckdb)
8. Create a minimal README.md with: project description (1
   paragraph), prerequisites (Python 3.12, uv), install steps,
   the disclaimer text from section 1.4.
9. Write tests/conftest.py with an empty `def pytest_configure(config): pass`
   and the @pytest.fixture for a tmp_path-based settings override.

Acceptance gates (run each, report PASS/FAIL):
- `uv sync` exits 0
- `uv run qivc --version` prints "0.1.0"
- `uv run pytest -q` passes (0 tests is acceptable)
- `uv run ruff check src tests` passes
- `uv run ruff format --check src tests` passes
- `uv run mypy src` passes (strict)

Stop after reporting acceptance results. Do not advance.
```

---

## Phase 1 Prompt — Data Layer

```
Execute Phase 1 (Data Layer) from PROJECT_BRIEF.md.

Read section 5 (Phase 1), section 6 (Agent Interface Contracts), and
section 7 (Data Source Configuration) before coding.

Build order — work through in this exact sequence:

1. SCHEMAS (src/qivc/schemas.py):
   Define Pydantic v2 models for every data type the agents return.
   At minimum:
   - InsiderTransaction (cik, name, title, ticker, shares, price,
     value_usd, transaction_date, filed_date, transaction_code,
     is_director, is_officer, is_ten_percent_owner)
   - InsiderHistory (cik, transactions: list[InsiderTransaction],
     years_of_history: int)
   - Fundamentals (ticker, fiscal_period, roa, ocf, delta_roa,
     ocf_gt_ni, delta_leverage, delta_liquidity, no_share_issuance,
     delta_gross_margin, delta_asset_turnover, gross_profit, total_assets)
   - Valuation (ticker, sector, gics_industry, forward_pe, ev_ebitda,
     p_tbv, p_affo, sector_median_metric_value)
   - ShortInterest (ticker, si_pct_float, prior_si_pct_float,
     report_date, direction: Literal["rising","flat","falling"])
   - EpsRevisions (ticker, delta_30d, delta_60d, delta_90d, accelerating: bool)
   - Liquidity (ticker, market_cap_usd, adv_20d_usd, next_earnings_date,
     has_pending_ma: bool)
   - MarketRegime (vix_60d_sma, credit_spread_bps, yield_curve_bps,
     value_growth_12m, regime: Literal["risk-on","risk-mid","risk-off"])
   All models: frozen=True, strict types, ConfigDict(extra="forbid").

2. EXCEPTIONS (src/qivc/exceptions.py):
   - QivcError (base)
   - QivcDataError (data fetch/parse failure)
   - QivcRateLimitError (specifically rate-limit-related)
   - QivcConfigError

3. EDGAR CLIENT (src/qivc/data/edgar_client.py):
   - Wrap edgartools with rate-limit guard
   - Use an asyncio.Semaphore-based throttle at 8 req/sec
   - Implement exponential backoff with jitter on 429/403
     (min 1s, max 60s, factor 2)
   - Call edgartools' set_identity() on instantiation
   - Expose async methods: get_form4_filings(lookback_days),
     get_insider_history(cik, years), get_fundamentals_10k(ticker),
     get_fundamentals_10q(ticker, periods=4)

4. AGENTS (one file each in src/qivc/data/):
   form4_agent.py: returns list[InsiderTransaction], filtered to code P
   insider_history_agent.py: per-CIK 3-year window
   fundamentals_agent.py: computes 9 Piotroski components +
     gross profit + total assets (DOES NOT compute F-Score —
     that's a filter, not data)
   valuation_agent.py: pulls forward_pe etc. via yfinance.info,
     plus calls sector_median_repo to get the median for ticker's sector
   short_interest_agent.py: yfinance for SI%, computes direction
     from last 2 reports
   estimates_agent.py: yfinance analyst revisions; if not
     available, returns deltas=None and consumer treats as UNVERIFIABLE
   liquidity_agent.py: market cap, ADV (20d), earnings calendar,
     M&A flag (search 8-K for "Item 1.01 Material Definitive
     Agreement" in last 60d as proxy)
   regime_agent.py: FRED via httpx (no API key needed for series
     CSV downloads at fred.stlouisfed.org/graph/fredgraph.csv?id=...)

5. FIXTURES (tests/fixtures/):
   - Save 3 real Form 4 filings as JSON: UNH (Apr 2026 cluster), FCN
     (May 2026 cluster), one TSLA single-officer purchase
   - Save 1 real 10-K for FTI Consulting (extract via edgartools,
     persist the parsed XBRL components)
   - Save 1 cached FRED response for each regime indicator
   To generate fixtures, write a one-shot script
   scripts/generate_fixtures.py that hits live APIs ONCE and
   persists; check the resulting files into the repo.

6. UNIT TESTS (tests/unit/data/):
   - test_edgar_client.py: rate-limit semaphore behavior (use
     fake clock); 429 retry; User-Agent header presence
   - test_form4_agent.py: parse fixture, verify code-P filter works
   - test_insider_history_agent.py: 3-year window correctness
   - test_fundamentals_agent.py: 9 components extracted from FTI
     fixture, hand-computed expected values
   - test_valuation_agent.py: mock yfinance response; verify
     sector-median lookup
   - test_short_interest_agent.py: direction computation edge cases
   - test_regime_agent.py: regime classification from sample
     indicator values (test all 3 regime states)
   Use `responses` library to mock httpx/requests calls.
   No live network in unit tests.

Acceptance gates:
- `uv run pytest tests/unit/data -q` passes
- `uv run pytest --cov=src/qivc/data --cov-report=term-missing tests/unit/data`
  shows ≥ 95% coverage
- `uv run mypy src/qivc/data src/qivc/schemas.py` passes strict
- `uv run ruff check src/qivc/data` passes
- No imports of `qivc.filters` from `qivc.data` (run a test
  that asserts this with importlib)

Commit message: "phase1: data layer with 8 agents + edgar rate-limit guard"

Stop after acceptance results.
```

---

## Phase 2 Prompt — Filter Layer

```
Execute Phase 2 (Filter Layer) from PROJECT_BRIEF.md.

Constraint: every filter is a PURE FUNCTION. No I/O. No imports from
qivc.data. Filters take typed inputs from schemas.py and return
FilterResult.

Build order:

1. Add FilterResult and CandidateInput to schemas.py:
   - FilterResult: {filter_name: str, passed: bool | None,
     metric_value: float | None, threshold: float | None,
     reason: str}
   - CandidateInput: composite of Fundamentals, Valuation,
     ShortInterest, EpsRevisions, Liquidity, and the
     list[InsiderTransaction] cluster

2. FSCORE FILTER (src/qivc/filters/fscore.py):
   def compute_fscore(f: Fundamentals) -> tuple[int, dict[str,int]]:
     Returns total score 0-9 and per-component dict.
   def apply(c: CandidateInput, threshold: int = 7) -> FilterResult

3. GPA FILTER (src/qivc/filters/gpa.py):
   def compute_gpa(f: Fundamentals) -> float:
     Returns gross_profit / total_assets
   def apply(c: CandidateInput, industry_median: float) -> FilterResult
   Note: industry_median is passed in (computed upstream by
   a separate repo); filter is pure.

4. VALUATION FILTER (src/qivc/filters/valuation.py):
   Implement the SECTOR_VALUATION_METRIC and SECTOR_VALUATION_PREMIUM
   dicts exactly per brief section 9.2.
   def apply(c: CandidateInput) -> FilterResult
   Selects metric based on c.valuation.sector, compares against
   sector_median * premium_allowance.

5. REVISIONS FILTER (src/qivc/filters/revisions.py):
   def apply(c: CandidateInput) -> FilterResult
   PASS if revisions.delta_90d >= 0; if delta_90d is None,
   passed=None (UNVERIFIABLE).

6. INSIDER CLASSIFIER (src/qivc/filters/insider_classifier.py):
   CRITICAL — implement exactly per CMP 2012.

   def classify(
       transaction: InsiderTransaction,
       history: InsiderHistory,
   ) -> Literal["opportunistic", "routine", "unclassified"]:
       # If history.years_of_history < 3 → "unclassified"
       # Else: look at history.transactions, group by calendar
       # month. If the transaction.transaction_date.month has
       # at least one trade in EACH of the prior 3 years
       # (year-1, year-2, year-3) → "routine"
       # Otherwise → "opportunistic"

   Test cases (must pass):
   - Insider with trades in May 2023, May 2024, May 2025 →
     new May 2026 trade is ROUTINE
   - Insider with trades in May 2023, May 2025 (gap in 2024)
     → new May 2026 trade is OPPORTUNISTIC
   - Insider with only 1 year of history → UNCLASSIFIED

7. CLUSTER DETECTOR (src/qivc/filters/cluster_detector.py):
   def detect_clusters(
       transactions: list[InsiderTransaction],
       classifications: dict[str, str],  # cik → classification
       window_days: int = 7,
   ) -> list[Cluster]:
       # Group transactions by ticker
       # Within each ticker, find 7-day windows containing
       # ≥3 opportunistic insiders (Track A)
       # Also find single C-suite opportunistic buys meeting
       # size threshold (Track B; size logic per brief A.Filter3)

8. LIQUIDITY FILTER (src/qivc/filters/liquidity.py):
   def apply(c: CandidateInput, target_position_usd: float) -> FilterResult
   Checks market_cap >= $300M AND adv_20d >= 20x target_position
   AND next_earnings_date is > 5 trading days away
   AND has_pending_ma is False.

9. SHORT INTEREST FILTER (src/qivc/filters/short_interest.py):
   Implement the 4-case logic from QIVC v2.0 spec:
   - SI < 5%, declining → PASS, no upgrade
   - SI 5-15%, declining → PASS, upgrade conviction flag
   - SI > 15%, rising → conditional: only PASS if
     extra_strength_signals provided (CEO buy ≥ $1M OR
     F-Score ≥ 8 OR GP/A top quartile)
   - SI > 15%, declining → PASS, upgrade conviction flag

10. REGIME FILTER (src/qivc/filters/regime.py):
    def apply(regime: MarketRegime) -> FilterResult
    risk-on → PASS
    risk-mid → PASS with annotation
    risk-off → FAIL (blocks new entries)

11. UNIT TESTS (tests/unit/filters/):
    - One test file per filter
    - F-Score: hand-build a fixture Fundamentals where 7/9
      components pass (boundary case at threshold=7)
    - GPA: hand-build a case where ticker GP/A = industry median
      (boundary)
    - CMP classifier: the three test cases above are MANDATORY
    - Cluster detector: 3 buys in 7 days = cluster; 3 buys
      spanning 8 days = no cluster; 2 buys + 1 routine = no cluster
    - Use hypothesis for property-based tests on F-Score
      (any valid components → score in [0,9])

12. IMPORT LINT TEST (tests/unit/filters/test_no_data_imports.py):
    Use importlib to verify no module in qivc.filters imports
    from qivc.data. If it does, fail.

Acceptance gates:
- `uv run pytest tests/unit/filters -q` passes

- ≥ 98% coverage on src/qivc/filters/
- All CMP classifier test cases pass
- Import-lint test passes
- mypy strict passes

Commit message: "phase2: 10 pure-function filters with CMP classifier"

Stop after acceptance.
```

---

## Phase 3 Prompt — Orchestration

```
Execute Phase 3 (Orchestration) from PROJECT_BRIEF.md.

Build LangGraph pipeline that runs Phase 1 agents and Phase 2
filters end-to-end against a list of candidate tickers.

1. STATE (src/qivc/orchestration/state.py):
   Pydantic model OR TypedDict (langgraph 1.0 supports both;
   prefer Pydantic for runtime validation):

   class PipelineState(BaseModel):
       run_id: str
       run_timestamp: datetime
       regime: MarketRegime | None
       # Per-ticker accumulators (dict keyed by ticker)
       transactions_by_ticker: dict[str, list[InsiderTransaction]]
       insider_histories: dict[str, InsiderHistory]
       fundamentals: dict[str, Fundamentals]
       valuations: dict[str, Valuation]
       short_interest: dict[str, ShortInterest]
       revisions: dict[str, EpsRevisions]
       liquidity: dict[str, Liquidity]
       # Filter outcomes (dict keyed by ticker → list[FilterResult])
       filter_results: dict[str, list[FilterResult]]
       # Final
       candidates: list[Candidate]
       rejected: list[RejectedCandidate]

2. NODES (src/qivc/orchestration/nodes.py):
   One node function per logical step. Each node:
   - Takes PipelineState, returns dict with updated fields
   - Wraps a data agent or a filter
   - Logs entry/exit with structlog (run_id, node_name, duration_ms)

   Required nodes:
   - regime_check (data) → sets state.regime; if risk-off and
     not --force flag, raises early-exit
   - ingest_form4 (data) → populates transactions_by_ticker
   - fetch_insider_histories (data, parallel) → for each unique CIK
   - fetch_fundamentals (data, parallel) → per ticker
   - fetch_valuation (data, parallel)
   - fetch_short_interest (data, parallel)
   - fetch_revisions (data, parallel)
   - fetch_liquidity (data, parallel)
   - apply_filters (filter) → per ticker, runs all 10 filters
   - apply_cluster_detection (filter)
   - apply_synthesis (synthesis, Phase 4) → placeholder for now

3. GRAPH (src/qivc/orchestration/graph.py):
   Build a StateGraph wiring nodes in this order:
   START → regime_check → ingest_form4 →
     [fan-out: fetch_*] (parallel via langgraph branching) →
     join → apply_cluster_detection → apply_filters →
     apply_synthesis → END

   Use SqliteSaver checkpointer:
       from langgraph.checkpoint.sqlite import SqliteSaver
       checkpointer = SqliteSaver.from_conn_string(
           settings.db_path
       )
   graph = builder.compile(checkpointer=checkpointer)

4. RUN AUDIT (src/qivc/storage/repositories.py):
   - DuckDB schema: table `run_audit` (run_id, node_name,
     entered_at, exited_at, duration_ms, result_summary)
   - Migration: create table if not exists on startup
   - Repository function: log_node_execution(...)
   - Wrap each node with a decorator that auto-logs to this table

5. CLI COMMAND (src/qivc/cli/main.py):
   `qivc screen` → invokes graph.invoke(initial_state)
   `qivc graph --output <path>` → writes graph.get_graph().draw_mermaid_png()

6. INTEGRATION TEST (tests/integration/test_end_to_end.py):
   - Use fixtures from Phase 1
   - Patch all DataAgent.fetch calls to return fixture data
   - Invoke graph
   - Assert: state.candidates contains expected ticker,
     state.rejected contains expected ticker with expected reason
   - Assert: run_audit table has rows for every node

7. CHECKPOINT RESUMABILITY TEST (tests/integration/test_resume.py):
   - Run graph until it would call fetch_fundamentals
   - Patch fetch_fundamentals to raise once
   - Verify state is checkpointed
   - Re-run with same run_id
   - Verify it resumes from fetch_fundamentals onward

Acceptance gates:
- `uv run pytest tests/integration -q` passes
- `uv run qivc graph --output /tmp/graph.png` produces a file
- Run-audit log shows entry for every node when running on
  fixture data
- Killing the process mid-test and resuming completes
  (manual verification, document in PHASE3_NOTES.md)

Commit message: "phase3: LangGraph orchestration with sqlite checkpointing"

Stop after acceptance.
```

---

## Phase 4 Prompt — Scoring & Risk Overlay

```
Execute Phase 4 (Scoring & Risk Overlay) from PROJECT_BRIEF.md.

1. CONVICTION SCORER (src/qivc/synthesis/conviction.py):
   Implement the 4-tier scoring matrix from PROJECT_BRIEF section
   5 (Phase 4):

   def score(
       fscore: int,
       insider_track_record: InsiderTrackRecord | None,
       valuation_discount_pct: float,
       cluster_intensity: ClusterIntensity,
   ) -> ConvictionScore:
       # Returns total points and per-dimension breakdown

   Dimensions and points:
   - F-Score: 7→0, 8→2, 9→3
   - Insider track record:
     - None or <2 prior buys → 0
     - 2-3 prior buys with positive 12m → 2
     - 4+ prior buys with positive 12m → 3
   - Valuation discount vs. sector median:
     - At median → 0
     - 10% below median → 2
     - 25%+ below median → 3
   - Cluster intensity:
     - 1 insider, single buy → 0
     - 2 insiders → 2
     - 3+ insiders, ≥1 C-suite → 3
     - 5+ insiders OR (CEO + CFO both) → 4

   Score → indicative size:
   - 0-3 → 3.0%
   - 4-6 → 5.0%
   - 7-9 → 7.0%
   - 10+ → 10.0%

2. RISK OVERLAY (src/qivc/synthesis/risk_overlay.py):
   def apply_regime_cap(
       indicative_size: float, regime: MarketRegime
   ) -> tuple[float, str]:
       # risk-on → no change
       # risk-mid → cap at 60% of indicative
       # risk-off → cap at 30% of indicative
       # Returns (adjusted_size, explanation)

   def apply_sector_cap(
       candidates: list[Candidate], sector_cap: float = 0.20
   ) -> list[Candidate]:
       # Group by GICS sector
       # If cumulative size exceeds 20%, flag candidates from
       # smallest conviction first as "EXCEEDS_SECTOR_CAP"

   def apply_subsector_cap(
       candidates: list[Candidate], subsector_cap: float = 0.12
   ) -> list[Candidate]:
       # Same logic at GICS industry group level

3. UPDATE SYNTHESIS NODE IN PHASE 3:
   apply_synthesis node now:
   - Computes conviction score for each surviving candidate
   - Applies regime cap
   - Applies sector cap → sub-sector cap
   - Populates final state.candidates

4. UNIT TESTS (tests/unit/synthesis/):
   - test_conviction.py: all boundary cases (3/4, 6/7, 9/10 score
     transitions); table-driven tests
   - test_risk_overlay.py:
     - regime cap: risk-mid halves; risk-off thirds
     - sector cap: 3 healthcare candidates totaling 22% → 1
       gets flagged
     - sub-sector cap: 2 regional bank candidates totaling 14%
       → 1 gets flagged

Acceptance gates:
- `uv run pytest tests/unit/synthesis -q` passes
- 100% coverage on conviction.py and risk_overlay.py (these
  are pure logic, no excuse for gaps)
- Integration test from Phase 3 now produces conviction scores
  in the final state

Commit message: "phase4: conviction scoring and regime/sector overlays"

Stop after acceptance.
```

---

## Phase 5 Prompt — CLI & Dossier Output

```
Execute Phase 5 (CLI & Dossier Output) from PROJECT_BRIEF.md.

1. DOSSIER BUILDER (src/qivc/synthesis/dossier.py):
   def build_markdown(
       run_report: RunReport, output_path: Path
   ) -> None:
       # Uses jinja2 template (add jinja2 to deps now)
       # Template at src/qivc/synthesis/templates/run_report.md.j2

   def build_json(
       run_report: RunReport, output_path: Path
   ) -> None:
       # Pretty-printed JSON, schema in schemas.py

2. RUN_REPORT SCHEMA (extend schemas.py):
   class RunReport(BaseModel):
       run_id: str
       generated_at: datetime
       regime: MarketRegime
       candidates: list[Candidate]
       rejected: list[RejectedCandidate]
       universe_size: int  # total tickers screened
       run_duration_seconds: float

3. MARKDOWN TEMPLATE (src/qivc/synthesis/templates/run_report.md.j2):
   Exactly the format specified in PROJECT_BRIEF section 5
   (Phase 5). Mandatory elements:
   - Disclaimer at top (verbatim from section 1.4)
   - Run header with regime + timestamp
   - One section per surviving candidate with full gate table
   - Appendix of rejected candidates

4. CLI COMMANDS (src/qivc/cli/main.py):
   `qivc screen [--ticker TICKER] [--force] [--output-dir DIR]`
     - Default: full universe scan
     - --ticker: single-ticker mode (skips Form 4 ingest,
       creates synthetic Cluster of size 1 for that ticker,
       runs all other gates)
     - --force: ignore regime block in risk-off
     - Output written to <output-dir>/<run_id>/{report.md, report.json}
   `qivc audit RUN_ID`
     - Reads run_audit and filter_results from DuckDB
     - Prints a per-ticker gate breakdown for that run
   `qivc regime`
     - Just runs RegimeAgent and prints current classification
       with all indicator values

5. END-TO-END TEST (tests/integration/test_cli.py):
   Use Typer's CliRunner.
   - Run `qivc screen --ticker UNH` with patched data layer
   - Assert: output directory created
   - Assert: report.md contains disclaimer text verbatim
   - Assert: report.json validates against RunReport schema
   - Assert: every gate appears in the markdown for UNH

Acceptance gates:
- `uv run qivc screen --ticker UNH` (with patched data) produces
  a valid Markdown + JSON output
- `uv run qivc regime` prints all 4 indicators + classification
- `uv run qivc audit <run_id>` works after a screen run
- All CLI tests pass

Commit message: "phase5: typer CLI + jinja markdown/json dossier output"

Stop after acceptance.
```

---

## Phase 6 Prompt — Tests & CI

```
Execute Phase 6 (Tests & CI) from PROJECT_BRIEF.md.

1. EXPAND COVERAGE TO 90%+ OVERALL:
   Run `uv run pytest --cov=src/qivc --cov-report=term-missing`
   For any file below 90%, add targeted tests covering the gaps.

2. ADD LIVE SMOKE TESTS (tests/live/test_live_smoke.py):
   Marked @pytest.mark.live, skipped by default.

   - test_edgar_user_agent_present: hit data.sec.gov with the
     real client, capture outgoing request, assert User-Agent
     header matches Settings.edgar_user_agent format
   - test_edgar_rate_limit_compliance: make 20 sequential calls,
     assert no 403/429 responses, total time > (20/8) seconds
   - test_form4_recent_filings: fetch last 24h of Form 4
     filings, assert non-empty result with valid InsiderTransaction
     schema
   - test_regime_agent_live: hit FRED, assert returns valid
     MarketRegime

   Document running these manually in README:
   `uv run pytest -m live` (warns user this uses live network)

3. PRE-COMMIT (.pre-commit-config.yaml):
   - ruff (lint + format)
   - mypy strict on src/qivc
   - pytest tests/unit (fast tests only)

4. CI (.github/workflows/ci.yml):
   - Trigger on push and PR
   - Matrix: Python 3.12, 3.13
   - Steps:
     - Checkout
     - Install uv
     - uv sync --frozen
     - ruff check, ruff format --check
     - mypy src
     - pytest tests/unit tests/integration --cov=src/qivc
     - Coverage gate: fail if < 90%
   - Cache: uv cache + .venv between runs

5. UPDATE README.md:
   - Full runbook from PROJECT_BRIEF section 10
   - "Live testing" section explaining the @live mark
   - "CI status" badge
   - Troubleshooting section: common SEC EDGAR errors and fixes
   - Disclaimer

Acceptance gates:
- `uv run pytest tests/unit tests/integration --cov=src/qivc`
  reports ≥ 90%
- CI passes on a fresh push
- `pre-commit run --all-files` passes
- README has all required sections
- Manual run of `uv run pytest -m live` against real network
  passes (do this once, save the output to PHASE6_LIVE_TEST.log,
  commit the log)

Commit message: "phase6: CI, pre-commit, live smoke tests, 90% coverage"

Stop after acceptance.
```

---

## Phase 7 Prompt — Backtesting Harness (Deferred)

```
ONLY EXECUTE PHASE 7 AFTER USER EXPLICITLY CONFIRMS PHASES 0-6
ARE WORKING AGAINST LIVE DATA.

Execute Phase 7 (Backtesting Harness) from PROJECT_BRIEF.md.

1. ADD DEPENDENCIES: vectorbt, pyarrow (Parquet)

2. POINT-IN-TIME DATA LAYER (src/qivc/backtest/pit.py):
   - Function get_form4_as_of(date: date, lookback_days: int=14)
     that returns only Form 4 filings that were FILED before
     `date` (not transaction-dated; filing-dated, per real-world
     information availability)
   - Function get_fundamentals_as_of(ticker, date) that returns
     the most recent 10-K/10-Q FILED before `date`
   - Function get_sector_median_as_of(sector, date) — this
     requires historical sector ETF constituents; if not
     available, document as "current proxy used; backtest is
     therefore subject to sector-composition lookahead bias"

3. BACKTEST HARNESS (src/qivc/backtest/harness.py):
   def run_backtest(
       start: date,
       end: date,
       rebalance_freq: Literal["W", "M"] = "M",
       initial_capital: float = 1_000_000,
       slippage_bps: float = 5,
       commission_bps: float = 1,
   ) -> BacktestResult:
       # Walk forward from start to end
       # On each rebalance date:
       #   Run the screen using PIT data as of that date
       #   Compare to current portfolio
       #   Generate trade list (buy new candidates, sell exits)
       #   Apply slippage + commission
       # Track equity curve, drawdown, factor exposure
       # Return BacktestResult with all metrics

4. METRICS (src/qivc/backtest/metrics.py):
   - CAGR, Sharpe, Sortino, max drawdown, calmar
   - Hit rate (% of names profitable over hold)
   - Average hold duration
   - Decomposition: alpha vs. Russell 2000 Value (IWN)

5. CLI: `qivc backtest --start 2020-01-01 --end 2025-12-31`
   Outputs equity_curve.csv, metrics.json, trades.csv into
   data/backtests/<id>/

6. TESTS:
   - Unit test PIT date logic (no future data leaks)
   - Integration test with a small synthetic universe over a
     known period

Acceptance gates:
- Backtest runs end-to-end on a small range
- Output metrics are reproducible (same seed → same numbers)
- No look-ahead bias in PIT functions (verified by tests)

Commit message: "phase7: vectorbt backtesting harness with PIT data"
```

---

## Closing Notes for Claude Code

After all 7 phases:

1. The user runs `uv run qivc screen` daily.
2. They inspect the Markdown report at `data/runs/<timestamp>/report.md`.
3. They use the dossier as research input, then do their own
   final verification before committing capital.
4. Periodically (monthly), they run `qivc backtest` with the
   current strategy parameters to verify the live screen is
   producing the kind of names the historical backtest
   validated.

**You are not done until a naive user can:**
- Clone the repo
- Follow README install steps
- Run `qivc screen` and see candidate output
- Understand the dossier without prior context

That is the final acceptance test.
