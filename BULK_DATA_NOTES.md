# Bulk Form 4 Historical Data — Source, Schema, and Known Limitations

This documents the **SEC Insider Transactions Data Sets** ingestion built in
Task 8.1 (`src/qivc/data/bulk_loader.py`, `scripts/bootstrap_bulk_data.py`),
which populates the `form4_historical` table. It exists because the prior
per-filing EDGAR API path was capped at 100 filings — examining <1% of the
Form 4 universe — invalidating earlier "0 candidate" conclusions.

---

## 1. Source

- **Dataset:** SEC Insider Transactions Data Sets — flattened Forms 3/4/5.
  https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
- **README:** https://www.sec.gov/files/insider_transactions_readme.pdf
- **Archive URL:**
  `https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{YYYY}q{N}_form345.zip`
- **Access:** requires an SEC `User-Agent` (same contact string as EDGAR). The
  server honours range requests (`Accept-Ranges: bytes`).
- **Size:** ~13 MB compressed / ~91 MB uncompressed per quarter (10 files).
  The full 2023q1–2026q1 download is ~170 MB compressed and completes in
  minutes, versus the ~47 h the per-filing API path would have taken.

### Publication schedule (observed)

The dataset is published **quarterly, ~7 days after quarter-end**:

| Quarter | Quarter-end | Archive `Last-Modified` | Lag |
|---|---|---|---|
| 2025q4 | 2025-12-31 | 2026-01-07 | 7 days |
| 2026q1 | 2026-03-31 | 2026-04-07 | 7 days |

(Quarters 2023q1–2025q1 carry a 2025-07 re-stamp from a dataset re-host, so
their headers don't reflect original publication.) The loader uses a
conservative **21-day** expected-publication pad (`_PUBLICATION_LAG_DAYS`) so
`bootstrap --refresh` never tries to fetch an unpublished quarter.

---

## 2. Tables used (3 of 8) and the join

All tables are tab-separated with a header row and join on `ACCESSION_NUMBER`.

| Table | Keys (per README) | Fields we read |
|---|---|---|
| `SUBMISSION.tsv` | `ACCESSION_NUMBER` | `FILING_DATE`, `ISSUERTRADINGSYMBOL` |
| `REPORTINGOWNER.tsv` | `ACCESSION_NUMBER`, `RPTOWNERCIK` | `RPTOWNERNAME`, `RPTOWNER_RELATIONSHIP`, `RPTOWNER_TITLE` |
| `NONDERIV_TRANS.tsv` | `ACCESSION_NUMBER`, `NONDERIV_TRANS_SK` | `TRANS_CODE`, `TRANS_DATE`, `TRANS_SHARES`, `TRANS_PRICEPERSHARE` |

Dates are formatted `DD-MON-YYYY` (e.g. `28-FEB-2024`), parsed with
`strptime(..., '%d-%b-%Y')`.

### Field mapping → `form4_historical`

| Column | Source | Notes |
|---|---|---|
| `cik` | `RPTOWNERCIK` | zero-padded as SEC stores it |
| `name` | `RPTOWNERNAME` | SEC raw casing (see §4) |
| `title` | `RPTOWNER_TITLE` | raw; blank for many directors (see §4) |
| `ticker` | `ISSUERTRADINGSYMBOL` | 100% populated in samples |
| `shares` | `TRANS_SHARES` | `CAST ... AS DOUBLE` |
| `price` | `TRANS_PRICEPERSHARE` | `CAST ... AS DOUBLE` |
| `value_usd` | `shares * price` | derived |
| `transaction_date` | `TRANS_DATE` | |
| `filed_date` | `FILING_DATE` | authoritative (see §4) |
| `transaction_code` | `TRANS_CODE` | always `'P'` in this table |
| `is_director` | `contains(RPTOWNER_RELATIONSHIP, 'Director')` | derived |
| `is_officer` | `contains(RPTOWNER_RELATIONSHIP, 'Officer')` | derived |
| `is_ten_percent_owner` | `contains(RPTOWNER_RELATIONSHIP, 'TenPercentOwner')` | derived |
| `accession_number` | `ACCESSION_NUMBER` | `(cik, accession)` = dedup key vs live EDGAR |
| `source_quarter` | (loader) | enables idempotent per-quarter re-import |

`RPTOWNER_RELATIONSHIP` is a comma-joined enum
(`Officer`, `Director`, `Director,Officer`, `TenPercentOwner`,
`Director,Officer,TenPercentOwner`, `Other`, …). The three tokens never appear
as substrings of one another, so `contains()` derivation is exact.

---

## 3. Filter: strict open-market purchases only

We keep transaction code `P` **and** positive price **and** positive shares:

```sql
TRANS_CODE = 'P'
AND TRY_CAST(TRANS_PRICEPERSHARE AS DOUBLE) > 0
AND TRY_CAST(TRANS_SHARES AS DOUBLE) > 0
```

A genuine open-market purchase always has positive price and shares; a zero or
blank on either is a data-quality issue or a misclassified gift. Across
2023q1–2026q1 this removed **45–109 rows per quarter** (~1–2% of P-transactions),
overwhelmingly `price = 0.0` rows. Sample of excluded rows is printed by the
bootstrap for eyeballing.

---

## 4. Schema equivalence vs. edgartools (verified)

`tests/live/test_bulk_record_matches_edgartools_record` imports the bulk rows
for AFLAC accession `0000004977-24-000017` (a clean single-owner, single-P
filing) and diffs them against the live edgartools parse. **Identical** on
`cik`, `ticker`, `shares` (1500.0), `price` (76.0), `transaction_date`
(2024-02-05), `transaction_code` (P), and all three relationship flags.

Three benign differences, documented and *not* asserted equal:

1. **`name`** — bulk keeps SEC's raw `"BOWERS WILLIAM P"`; edgartools normalises
   to `"William P Bowers"`. **Insider identity is matched on CIK** (identical),
   never on name, so this is cosmetic.
2. **`title`** — bulk uses the raw `RPTOWNER_TITLE`, which is blank for many
   directors; edgartools synthesises `"Director"`. The `is_director` flag is
   identical in both, so downstream logic is unaffected.
3. **`filed_date`** — the bulk value (`2024-02-07`) is **authoritative and
   correct**. The edgartools value observed via the single-filing `find()` path
   was today's date, an artifact of that fetch path; the bulk store is the more
   reliable source of `filed_date`.

---

## 5. Known limitation: multi-owner fan-out (cross-join)

The SEC schema links transactions to owners **only** via `ACCESSION_NUMBER` —
`NONDERIV_TRANS` has no per-transaction owner key (its PK is
`ACCESSION_NUMBER` + `NONDERIV_TRANS_SK`, with no `RPTOWNERCIK`). So for a
multi-owner (joint) filing we cannot attribute a transaction to one specific
owner, and we emit **one record per (transaction × owner)** — a cross-join.

> **Magnitude is larger than the filing-level rate suggests — flag for Task 8.2.**
> Co-filer filings are ~2.2% of *all* filings, but among **P-code purchase**
> filings specifically they are **~18%** (538 of 3,017 P-accessions in 2024q1),
> and some are investment-fund group filings with **up to 10 affiliated
> co-owners**. The net effect is a **~1.5× record inflation**: the 13-quarter
> load produced **109,261 records from ~71,800 kept P-transactions**.

Implications for downstream consumers (to be handled in Task 8.2, not 8.1):

- **CMP clustering** counts distinct opportunistic *buyers* in a 7-day window.
  Fan-out can inflate the buyer count for a single joint purchase (e.g. a fund
  filing under 10 affiliated CIKs would look like 10 buyers), risking spurious
  Track-A (≥3 distinct buyers) clusters. Consider de-duplicating by
  `accession_number` (count distinct filings, not owners) or collapsing
  affiliated co-filers when reading from `form4_historical`.

The live edgartools path, by contrast, attributes all of a filing's
transactions to a single chosen owner (first non-company owner). The two
sources therefore **diverge only on multi-owner filings**; on the ~82% of
single-owner P-filings they are identical (per §4).

## 6. Known limitation: raw price anomalies

A handful of rows carry implausible prices straight from the SEC source — e.g.
ticker `REEMF`, `TRANS_PRICEPERSHARE = 29,326,030` (→ a ~$7e15 `value_usd`).
~9 of 5,509 kept P-transactions in 2024q1 had `price > $10,000`. The strict
filter only excludes non-positive prices, so these survive. **No price ceiling
is applied** (out of scope for Task 8.1; clarification #1 covered only
zero/blank). Downstream value-based gates (Track B's $250k threshold) should be
aware that a small number of records have garbage `value_usd`; a sanity bound is
a candidate for Task 8.2/8.3.

---

## 7. Bulk ↔ live cutover (for the daily screen, Task 8.2)

`form4_historical` covers 2023q1 through the most recent **published** quarter
(tracked in `bulk_load_progress`). The daily screen's trailing 14-day window
is sourced from `form4_historical` where available, **falling back to live
EDGAR for filings filed after the most recent bulk quarter's end date**.
Records overlapping both sources are de-duplicated by `(cik, accession_number)`,
**bulk winning** as the authoritative dataset. (Wiring this into
`form4_agent.py` and removing `_GLOBAL_SCAN_LIMIT` is Task 8.2.)

---

## 8. Operations

```bash
# One-time bootstrap (2023q1 → latest published; idempotent, resumable)
uv run python scripts/bootstrap_bulk_data.py
# or: uv run qivc bootstrap

# Weekly refresh (loads the next quarter only if published; cron-safe no-op otherwise)
uv run python scripts/bootstrap_bulk_data.py --refresh
# or: uv run qivc bootstrap --refresh
```

- **Idempotent / resumable:** each quarter is recorded in `bulk_load_progress`
  with `status='complete'`; a re-run skips completed quarters, and a quarter is
  re-imported cleanly (rows deleted by `source_quarter` first) if forced.
  Downloads stream to a `.part` file and atomically rename, so an interrupted
  download never looks complete.
- **Cache:** archives are kept under `data/bulk_cache/` and reused.

### Validation snapshot (2023q1–2026q1, loaded 2026-06)

```
total records:    109,261
distinct tickers:   4,464
filed_date range: 2023-01-03 .. 2026-03-31
per-year (filed):  2023: 38,854 | 2024: 32,528 | 2025: 30,332 | 2026: 7,547
quarters loaded:   13 (all complete)
```
