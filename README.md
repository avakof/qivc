# QIVC v2.0

[![CI](https://github.com/avakof/qivc/actions/workflows/ci.yml/badge.svg)](https://github.com/avakof/qivc/actions/workflows/ci.yml)

QIVC v2.0 is a Python-based long-only equity research screening system that surfaces stock candidates by applying a sequential battery of quantitative filters — Piotroski F-Score, gross profitability, sector-appropriate valuation, analyst EPS revisions, insider conviction (Cohen-Malloy-Pomorski classification), liquidity, short-interest direction, and macro regime — and produces fully-attributed dossiers in Markdown and JSON for human review.

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

### Daily run

```bash
uv run qivc screen
```

Output is written to `data/runs/<run_id>/report.md` and `report.json`.

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

Prints the per-node timings and the per-ticker gate breakdown recorded in DuckDB.

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
