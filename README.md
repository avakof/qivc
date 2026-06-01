# QIVC v2.0

[![CI](https://github.com/avakof/qivc/actions/workflows/ci.yml/badge.svg)](https://github.com/avakof/qivc/actions/workflows/ci.yml)

QIVC v2.0 — the Quality-Insider Value Composite — is a Python-based long-only equity research screening system. It surfaces stock candidates that combine **quality** (Piotroski F-Score, gross profitability), **value** (sector-appropriate valuation), and **opportunistic insider conviction** (clusters of open-market purchases, classified per Cohen-Malloy-Pomorski 2012), gated further by analyst EPS revisions, liquidity, short-interest direction, and macro regime. It produces fully-attributed dossiers in Markdown and JSON for human review.

Design decisions, deviations from the brief, and open research questions live in **[STRATEGY_NOTES.md](STRATEGY_NOTES.md)**; the full specification is **[PROJECT_BRIEF.md](PROJECT_BRIEF.md)**.

> **Disclaimer:** This is research output, not investment advice. No personal recommendation is intended. Verify all signals independently before any investment decision.

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)

## Runbook

### One-time setup

```bash
git clone <repo-url>
cd qivc
uv sync
cp .env.example .env
# edit .env with your EDGAR user agent: "YourName your.email@example.com"
```

### One-time bootstrap of historical insider data (used for CMP classification)

```bash
uv run python scripts/bootstrap_bulk_data.py
# Takes 30-60 minutes; downloads SEC bulk Form 4 dumps for the prior 3 years
```

### Daily workflow

```bash
uv run qivc screen
```

1. `qivc screen` runs the full pipeline and writes `data/runs/<run_id>/report.md`
   and `report.json`. Sanity-check results are folded into the report; a
   `⚠️ SANITY CHECK FAILED` header appears if anything looks anomalous.
2. **Read `report.md`** — surviving candidates with per-gate attribution up top,
   rejected names in the appendix.
3. **Review the names** yourself before acting — this is research output, not a
   recommendation (see disclaimer).

### Check the current regime

```bash
uv run qivc regime
```

### Single-ticker investigation

```bash
uv run qivc screen --ticker UNH
```

Single-ticker mode skips Form 4 ingest, synthesises a size-1 cluster for the
ticker, and runs every other gate so you can inspect quality/valuation/liquidity
in isolation.

### Audit a past run

```bash
uv run qivc audit <run_id>
```

Prints the per-node timings, the per-ticker gate breakdown, and per-insider CMP
classifications recorded in DuckDB.

### Sanity-check a run

```bash
uv run qivc sanity [--run-id <run_id>]   # default: most recent run
```

Verifies dossier invariants on the run's survivors (market cap ≥ $300M, positive
ROA, opportunistic-only clusters, all required gates passed, ≥1 Form 4 buyer) and
flags the silent-failure pattern (zero candidates from a non-completed run). Exits
non-zero and names offenders on any violation.

### Backtest (point-in-time)

```bash
uv run qivc backtest --start 2024-01-01 --end 2024-06-30 \
  --freq M --universe AAPL,MSFT,JNJ
```

Walks rebalance dates using only filing-date point-in-time data, simulates with
vectorbt, and writes `equity_curve.csv`, `metrics.json`, and `trades.csv` to
`data/backtests/<id>/`. See [STRATEGY_NOTES.md](STRATEGY_NOTES.md) and
[PHASE7_NOTES.md](PHASE7_NOTES.md) for the documented limitations (survivor bias,
sector-median look-ahead, quality-core screen subset).

### Weekly maintenance

```bash
uv run python scripts/refresh_sector_medians.py
```

### Scheduling

Daily run via cron at 9:00 ET (post-market-open, after overnight Form 4 filings settle):

```cron
0 9 * * 1-5 cd /path/to/qivc && uv run qivc screen >> data/runs/cron.log 2>&1
```

---

## Testing

```bash
# Fast unit + integration suite (live tests excluded by default)
uv run pytest

# With coverage (CI gate is ≥ 90%)
uv run pytest tests/unit tests/integration --cov=src/qivc --cov-report=term-missing
```

### Live testing

A handful of smoke tests hit the real SEC EDGAR, FRED, and yfinance endpoints.
They carry the `@pytest.mark.live` marker and are **skipped by default**
(`pyproject.toml` sets `addopts = "-m 'not live'"`). Run them manually:

```bash
# ⚠️ Uses the live network — real requests to SEC EDGAR / FRED / yfinance.
uv run pytest -m live
```

The live suite verifies:
- the configured `User-Agent` is present on outgoing SEC requests and accepted;
- 20 sequential calls trip no 403/429 and are paced under the rate limit;
- recent Form 4 filings parse into the `InsiderTransaction` schema;
- `RegimeAgent` returns a valid `MarketRegime` from live FRED.

A captured run is saved in [PHASE6_LIVE_TEST.log](PHASE6_LIVE_TEST.log).

### Pre-commit

```bash
uv run pre-commit install      # one-time
uv run pre-commit run --all-files
```

Hooks: ruff (lint + format), mypy strict on `src/qivc`, and the fast unit suite.

---

## Troubleshooting (SEC EDGAR)

| Symptom | Cause | Fix |
|---|---|---|
| `403 Forbidden` from EDGAR | Missing / malformed `User-Agent` | Set `QIVC_EDGAR_USER_AGENT="Name email@domain.com"` in `.env`. SEC requires a real contact string. |
| `429 Too Many Requests` | Exceeded ~10 req/sec | Lower `QIVC_EDGAR_RATE_LIMIT_RPS` (default 8). The client already backs off exponentially. |
| `No 10-K XBRL data found` | Filing predates XBRL or is a foreign filer | Expected for some tickers; the candidate is marked `UNVERIFIABLE`, not dropped. |
| Empty screen / no candidates | No qualifying insider clusters in the lookback window | Widen `QIVC_FORM4_LOOKBACK_DAYS` or investigate a single name with `--ticker`. |
| `risk-off` blocks the run | Macro regime gate | Pass `--force` to override (research only). |
| SSL / certificate errors on a corporate network | Proxy / SSL inspection | edgartools supports `configure_http(use_system_certs=True)`; export your corporate CA bundle. |

---

## Configuration

All settings are environment-driven (see `.env.example`). Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `QIVC_EDGAR_USER_AGENT` | — (required) | SEC contact string |
| `QIVC_EDGAR_RATE_LIMIT_RPS` | `8` | EDGAR request rate cap |
| `QIVC_FORM4_LOOKBACK_DAYS` | `14` | Form 4 ingest window |
| `QIVC_CMP_HISTORY_YEARS` | `3` | Insider-classification lookback |
| `QIVC_CLUSTER_WINDOW_DAYS` | `7` | Track-A cluster window |
| `QIVC_DB_PATH` | `data/duckdb/qivc.db` | DuckDB path |
| `QIVC_OUTPUT_DIR` | `data/runs` | Dossier output directory |

---

> **Disclaimer:** This is research output, not investment advice. No personal recommendation is intended. Verify all signals independently before any investment decision.
