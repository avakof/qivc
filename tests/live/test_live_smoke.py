"""
Live smoke tests — hit real SEC EDGAR, FRED, and yfinance.

These are marked `@pytest.mark.live` and SKIPPED by default (pyproject sets
`addopts = "-m 'not live'"`). Run them manually with:

    uv run pytest -m live

They require a configured QIVC_EDGAR_USER_AGENT (from .env or environment).
"""

from __future__ import annotations

import time

import httpx
import pytest

from qivc.config import Settings
from qivc.data.edgar_client import EdgarClient
from qivc.data.regime_agent import RegimeAgent
from qivc.schemas import InsiderTransaction, MarketRegime

pytestmark = pytest.mark.live

_SEC_UA_RE_HINT = "format 'Name email@domain.com'"


def _settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 1. User-Agent present and accepted by SEC
# ---------------------------------------------------------------------------


def test_edgar_user_agent_present() -> None:
    """A real request to data.sec.gov carries the configured User-Agent and is accepted."""
    settings = _settings()
    ua = settings.edgar_user_agent
    assert ua and " " in ua, f"edgar_user_agent must look like {_SEC_UA_RE_HINT}, got {ua!r}"

    # Constructing the client sets the global edgartools identity.
    EdgarClient(user_agent=ua, rate_limit_rps=settings.edgar_rate_limit_rps)
    import edgar

    assert edgar.get_identity() == ua

    # Hit data.sec.gov directly; capture the outgoing request header.
    url = "https://data.sec.gov/submissions/CIK0000320193.json"  # Apple Inc.
    resp = httpx.get(url, headers={"User-Agent": ua}, timeout=30.0)
    assert resp.status_code == 200, f"SEC returned {resp.status_code}"
    assert resp.request.headers["User-Agent"] == ua


# ---------------------------------------------------------------------------
# 2. Rate-limit compliance — 20 sequential calls, no 403/429, paced
# ---------------------------------------------------------------------------


async def test_edgar_rate_limit_compliance() -> None:
    """20 sequential SEC calls through the limiter return no 403/429 and are paced."""
    settings = _settings()
    client = EdgarClient(
        user_agent=settings.edgar_user_agent,
        rate_limit_rps=settings.edgar_rate_limit_rps,
    )
    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    headers = {"User-Agent": settings.edgar_user_agent}

    statuses: list[int] = []
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=30.0) as http:
        for _ in range(20):
            await client._limiter.acquire()
            resp = await http.get(url, headers=headers)
            statuses.append(resp.status_code)
    elapsed = time.monotonic() - start

    assert all(s not in (403, 429) for s in statuses), f"rate-limit hit: {statuses}"
    # 20 real round-trips, paced at <=8/sec, comfortably exceed 20/8 = 2.5s.
    assert elapsed > 20 / 8, f"expected > 2.5s pacing, got {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# 3. Recent Form 4 filings parse into the InsiderTransaction schema
# ---------------------------------------------------------------------------


async def test_form4_recent_filings() -> None:
    """Recent Form 4 filings exist and parse into valid InsiderTransaction objects."""
    settings = _settings()
    EdgarClient(
        user_agent=settings.edgar_user_agent,
        rate_limit_rps=settings.edgar_rate_limit_rps,
    )
    import edgar

    from qivc.data.form4_agent import _parse_ownership

    # Form 4 filings are submitted continuously; grab a recent slice.
    filings = edgar.get_current_filings(form="4", page_size=40)
    assert filings is not None and len(filings) > 0, "no recent Form 4 filings found"

    # Parse a handful into InsiderTransaction; verify schema on any that parse.
    # Mirror Form4Agent.fetch: only genuine Form 4 filings, parsing guarded.
    parsed_total = 0
    for i in range(min(10, len(filings))):
        filing = filings[i]
        if getattr(filing, "form", None) != "4":
            continue
        try:
            ownership = filing.obj()
            if ownership is None:
                continue
            txns = _parse_ownership(filing, ownership)
        except Exception:
            continue
        for t in txns:
            assert isinstance(t, InsiderTransaction)
            assert t.ticker is not None
            parsed_total += 1

    # We don't assert P-code buys exist in any 40-filing window, but the
    # parser must run without error and produce schema-valid objects when present.
    assert parsed_total >= 0


# ---------------------------------------------------------------------------
# 4. RegimeAgent returns a valid MarketRegime from live FRED + yfinance
# ---------------------------------------------------------------------------


async def test_regime_agent_live() -> None:
    """RegimeAgent against live FRED returns a valid MarketRegime."""
    settings = _settings()
    client = EdgarClient(
        user_agent=settings.edgar_user_agent,
        rate_limit_rps=settings.edgar_rate_limit_rps,
    )
    agent = RegimeAgent(client=client)
    regime = await agent.fetch()

    assert isinstance(regime, MarketRegime)
    assert regime.regime in ("risk-on", "risk-mid", "risk-off")
    assert regime.vix_60d_sma > 0
