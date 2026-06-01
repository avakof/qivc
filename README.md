# QIVC v2.0

QIVC v2.0 is a Python-based long-only equity research screening system that surfaces stock candidates by applying a sequential battery of quantitative filters — Piotroski F-Score, gross profitability, sector-appropriate valuation, analyst EPS revisions, insider conviction (Cohen-Malloy-Pomorski classification), liquidity, short-interest direction, and macro regime — and produces fully-attributed dossiers in Markdown and JSON for human review.

> **Disclaimer:** This is research output, not investment advice. No personal recommendation is intended. Verify all signals independently before any investment decision.

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)

## Installation

```bash
git clone <repo-url>
cd qivc
uv sync
cp .env.example .env
# Edit .env — set QIVC_EDGAR_USER_AGENT to "YourName your.email@example.com"
```

## One-time bootstrap (downloads 3 years of SEC bulk Form 4 data, ~30-60 min)

```bash
uv run python scripts/bootstrap_bulk_data.py
```

## Usage

```bash
# Daily screen (full pipeline)
uv run qivc screen

# Single-ticker investigation
uv run qivc screen --ticker UNH

# Print current market regime
uv run qivc regime

# Audit a past run
uv run qivc audit <run_id>

# Weekly maintenance — refresh sector medians
uv run python scripts/refresh_sector_medians.py
```

## Running tests

```bash
uv run pytest -q
# Live smoke tests (hits real EDGAR — run manually)
uv run pytest -m live
```
