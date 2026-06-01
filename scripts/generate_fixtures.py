"""
One-shot script — runs ONCE to generate JSON fixtures for tests.
Hits live EDGAR and FRED APIs. Output goes to tests/fixtures/.

Usage:
    uv run python scripts/generate_fixtures.py
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests" / "fixtures"
FIXTURES.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _json_default(obj: object) -> object:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Cannot serialise {type(obj)}")


def _save(name: str, data: object) -> None:
    path = FIXTURES / name
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=_json_default)
    print(f"  saved {path.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# Form 4 fixtures
# ---------------------------------------------------------------------------


def _ownership_to_dict(filing: object, ownership: object) -> dict:  # type: ignore[type-arg]
    """Convert an Ownership object to a JSON-serialisable dict."""
    issuer = getattr(ownership, "issuer", None)
    reporting_owners = getattr(ownership, "reporting_owners", None)
    nd_table = getattr(ownership, "non_derivative_table", None)

    owners_data = []
    if reporting_owners and hasattr(reporting_owners, "owners"):
        for o in reporting_owners.owners:
            owners_data.append(
                {
                    "cik": str(getattr(o, "cik", "") or ""),
                    "name": str(getattr(o, "name", "") or ""),
                    "officer_title": str(getattr(o, "officer_title", "") or ""),
                    "is_director": bool(getattr(o, "is_director", False)),
                    "is_officer": bool(getattr(o, "is_officer", False)),
                    "is_ten_pct_owner": bool(getattr(o, "is_ten_pct_owner", False)),
                    "is_company": bool(getattr(o, "is_company", False)),
                }
            )

    transactions = []
    if nd_table is not None and not getattr(nd_table, "empty", True):
        try:
            nd_txns = getattr(nd_table, "transactions", None)
            df = (
                nd_txns.data
                if nd_txns is not None and not getattr(nd_txns, "empty", True)
                else None
            )
            if df is not None:
                for _, row in df.iterrows():
                    transactions.append(
                        {
                            "Code": str(row.get("Code", "")),
                            "Date": str(row.get("Date", "")),
                            "Shares": float(row.get("Shares", 0) or 0),
                            "Price": float(row.get("Price", 0) or 0),
                            "AcquiredDisposed": str(row.get("AcquiredDisposed", "")),
                            "Security": str(row.get("Security", "")),
                            "Remaining": float(row.get("Remaining", 0) or 0),
                        }
                    )
        except Exception as e:
            print(f"    warning: could not read transactions: {e}")

    filed = str(getattr(filing, "filed", "")) if filing else ""

    return {
        "filed": filed,
        "issuer": {
            "ticker": str(getattr(issuer, "ticker", "") if issuer else ""),
            "name": str(getattr(issuer, "name", "") if issuer else ""),
            "cik": str(getattr(issuer, "cik", "") if issuer else ""),
        },
        "reporting_owners": owners_data,
        "non_derivative_transactions": transactions,
    }


def fetch_form4_fixtures() -> None:
    import edgar

    edgar.set_identity(
        os.environ.get("QIVC_EDGAR_USER_AGENT", "QIVC fixture-gen fixture@qivc.internal")
    )

    targets = [
        ("UNH", 60, "form4_unh.json"),
        ("FCN", 60, "form4_fcn.json"),
        ("TSLA", 60, "form4_tsla.json"),
    ]

    for ticker, lookback_days, filename in targets:
        print(f"  Fetching Form 4 for {ticker}...")
        try:
            from datetime import timedelta

            end = date.today()
            start = end - timedelta(days=lookback_days)
            company = edgar.Company(ticker)
            filings_obj = company.get_filings(form="4", date=f"{start}:{end}")
            if filings_obj is None or len(filings_obj) == 0:
                print(f"    No Form 4 filings found for {ticker}; saving empty fixture")
                _save(filename, [])
                continue

            all_records = []
            # Take up to 5 filings per company
            for i in range(min(5, len(filings_obj))):
                filing = filings_obj[i]
                try:
                    ownership = filing.obj()
                    if ownership is not None:
                        all_records.append(_ownership_to_dict(filing, ownership))
                except Exception as e:
                    print(f"    warning filing {i}: {e}")
            _save(filename, all_records)
        except Exception as e:
            print(f"    ERROR for {ticker}: {e}")
            _save(filename, [])


# ---------------------------------------------------------------------------
# 10-K fundamentals fixture (FTI Consulting)
# ---------------------------------------------------------------------------


def fetch_tenk_fixture() -> None:
    import edgar

    edgar.set_identity(
        os.environ.get("QIVC_EDGAR_USER_AGENT", "QIVC fixture-gen fixture@qivc.internal")
    )

    print("  Fetching 10-K for FCN (FTI Consulting)...")
    try:
        company = edgar.Company("FCN")
        filings_obj = company.get_filings(form="10-K")
        if filings_obj is None or len(filings_obj) == 0:
            print("    No 10-K found; saving empty fixture")
            _save("tenk_fcn.json", {})
            return

        filing = filings_obj[0]
        fin = edgar.Financials.extract(filing)
        if fin is None:
            print("    Financials.extract returned None")
            _save("tenk_fcn.json", {})
            return

        data = {
            "ticker": "FCN",
            "net_income": _safe_val(fin.get_net_income()),
            "total_assets": _safe_val(fin.get_total_assets()),
            "operating_cash_flow": _safe_val(fin.get_operating_cash_flow()),
            "revenue": _safe_val(fin.get_revenue()),
            "operating_income": _safe_val(fin.get_operating_income()),
            "shares_outstanding_basic": _safe_val(fin.get_shares_outstanding_basic()),
        }

        # Prior year — take second filing
        if len(filings_obj) > 1:
            filing_prior = filings_obj[1]
            fin_prior = edgar.Financials.extract(filing_prior)
            if fin_prior is not None:
                data["prior"] = {
                    "net_income": _safe_val(fin_prior.get_net_income()),
                    "total_assets": _safe_val(fin_prior.get_total_assets()),
                    "operating_cash_flow": _safe_val(fin_prior.get_operating_cash_flow()),
                    "revenue": _safe_val(fin_prior.get_revenue()),
                    "operating_income": _safe_val(fin_prior.get_operating_income()),
                    "shares_outstanding_basic": _safe_val(fin_prior.get_shares_outstanding_basic()),
                }

        _save("tenk_fcn.json", data)
    except Exception as e:
        print(f"    ERROR: {e}")
        _save("tenk_fcn.json", {})


def _safe_val(val: object) -> object:
    if val is None:
        return None
    try:
        f = float(val)  # type: ignore[arg-type]
        return None if f != f else f  # NaN guard
    except (TypeError, ValueError):
        return str(val)


# ---------------------------------------------------------------------------
# FRED regime indicator fixtures
# ---------------------------------------------------------------------------


def fetch_fred_fixtures() -> None:
    import httpx

    series = {
        "VIXCLS": "fred_vix.json",
        "BAA10Y": "fred_baa10y.json",
        "T10Y3M": "fred_t10y3m.json",
    }

    for series_id, filename in series.items():
        print(f"  Fetching FRED {series_id}...")
        try:
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
            resp = httpx.get(url, timeout=30.0)
            resp.raise_for_status()
            rows = []
            reader = csv.reader(io.StringIO(resp.text))
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    with contextlib.suppress(ValueError):
                        rows.append({"date": row[0], "value": float(row[1])})
            # Keep last 120 rows to keep fixture size manageable
            _save(filename, rows[-120:])
        except Exception as e:
            print(f"    ERROR: {e}")
            _save(filename, [])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Generating fixtures ===")
    print("\n[1/3] Form 4 filings (UNH, FCN, TSLA)")
    fetch_form4_fixtures()
    print("\n[2/3] FTI Consulting 10-K fundamentals")
    fetch_tenk_fixture()
    print("\n[3/3] FRED regime indicators")
    fetch_fred_fixtures()
    print("\nDone.")
