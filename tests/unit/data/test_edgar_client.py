"""Unit tests for EdgarClient — rate limiter, retry logic, user-agent."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

import pytest

from qivc.data.edgar_client import EdgarClient, _RateLimiter
from qivc.exceptions import QivcDataError, QivcRateLimitError

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


async def test_rate_limiter_blocks_at_capacity() -> None:
    """After rps acquisitions the semaphore should be fully consumed."""
    limiter = _RateLimiter(rps=3)
    callbacks: list[Any] = []

    loop = asyncio.get_event_loop()
    with mock.patch.object(loop, "call_later", side_effect=lambda _d, cb: callbacks.append(cb)):
        for _ in range(3):
            await limiter.acquire()

    assert limiter._sem._value == 0  # type: ignore[attr-defined]


async def test_rate_limiter_releases_after_callback() -> None:
    """Manually firing the call_later callback should release one slot."""
    limiter = _RateLimiter(rps=2)
    callbacks: list[Any] = []

    loop = asyncio.get_event_loop()
    with mock.patch.object(loop, "call_later", side_effect=lambda _d, cb: callbacks.append(cb)):
        await limiter.acquire()
        await limiter.acquire()

    assert limiter._sem._value == 0  # type: ignore[attr-defined]
    callbacks[0]()
    assert limiter._sem._value == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Retry / back-off
# ---------------------------------------------------------------------------


async def test_run_with_retry_succeeds_immediately() -> None:
    with mock.patch("edgar.set_identity"):
        client = EdgarClient(user_agent="Test User test@example.com", rate_limit_rps=10)

    with mock.patch.object(client, "_run", new=mock.AsyncMock(return_value="ok")):
        result = await client._run_with_retry(lambda: None)

    assert result == "ok"


async def test_run_with_retry_retries_on_429() -> None:
    """Should retry after a 429-like exception and eventually succeed."""
    with mock.patch("edgar.set_identity"):
        client = EdgarClient(user_agent="Test User test@example.com", rate_limit_rps=10)

    attempt = 0

    async def _flaky_run(func: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            raise Exception("HTTP 429 rate limit")
        return "success"

    with (
        mock.patch.object(client, "_run", side_effect=_flaky_run),
        mock.patch("asyncio.sleep", new=mock.AsyncMock()) as mock_sleep,
    ):
        result = await client._run_with_retry(lambda: None)

    assert result == "success"
    assert attempt == 3
    assert mock_sleep.call_count == 2


async def test_run_with_retry_exhausts_raises_rate_limit_error() -> None:
    """After all retries, QivcRateLimitError must be raised."""
    with mock.patch("edgar.set_identity"):
        client = EdgarClient(user_agent="Test User test@example.com", rate_limit_rps=10)

    async def _always_429(func: Any, *args: Any, **kwargs: Any) -> Any:
        raise Exception("HTTP 429 too many requests")

    with (
        mock.patch.object(client, "_run", side_effect=_always_429),
        mock.patch("asyncio.sleep", new=mock.AsyncMock()),
        pytest.raises(QivcRateLimitError),
    ):
        await client._run_with_retry(lambda: None)


async def test_run_with_retry_non_rate_limit_raises_data_error() -> None:
    """Non-429 exceptions must propagate as QivcDataError without retry."""
    with mock.patch("edgar.set_identity"):
        client = EdgarClient(user_agent="Test User test@example.com", rate_limit_rps=10)

    async def _bad_run(func: Any, *args: Any, **kwargs: Any) -> Any:
        raise Exception("connection refused")

    with mock.patch.object(client, "_run", side_effect=_bad_run), pytest.raises(QivcDataError):
        await client._run_with_retry(lambda: None)


# ---------------------------------------------------------------------------
# User-Agent
# ---------------------------------------------------------------------------


def test_edgar_client_sets_identity() -> None:
    """EdgarClient.__init__ must call edgar.set_identity with the supplied agent string."""
    with mock.patch("edgar.set_identity") as mock_set:
        client = EdgarClient(user_agent="My App my@email.com", rate_limit_rps=8)

    mock_set.assert_called_once_with("My App my@email.com")
    assert client.user_agent == "My App my@email.com"


# ---------------------------------------------------------------------------
# EdgarClient public method coverage
# ---------------------------------------------------------------------------


async def _make_client() -> EdgarClient:
    with mock.patch("edgar.set_identity"):
        return EdgarClient(user_agent="Test test@test.com", rate_limit_rps=10)


async def test_get_form4_filings_with_ticker() -> None:
    client = await _make_client()
    mock_filings = mock.MagicMock()
    mock_company = mock.MagicMock()
    mock_company.get_filings.return_value = mock_filings

    with (
        mock.patch("edgar.Company", return_value=mock_company),
        mock.patch.object(client, "_run", new=mock.AsyncMock(return_value=mock_filings)),
    ):
        result = await client.get_form4_filings(ticker="UNH", lookback_days=14)

    assert result is mock_filings


async def test_get_form4_filings_global_scan() -> None:
    client = await _make_client()
    mock_results = mock.MagicMock()

    with mock.patch.object(client, "_run", new=mock.AsyncMock(return_value=mock_results)):
        result = await client.get_form4_filings(ticker=None, lookback_days=7)

    assert result is mock_results


async def test_get_insider_history() -> None:
    client = await _make_client()
    mock_filings = mock.MagicMock()
    mock_entity = mock.MagicMock()

    with (
        mock.patch("edgar.Entity", return_value=mock_entity),
        mock.patch.object(client, "_run", new=mock.AsyncMock(return_value=mock_filings)),
    ):
        result = await client.get_insider_history(cik="12345", years=3)

    assert result is mock_filings


async def test_get_financials_10k_returns_list() -> None:
    client = await _make_client()
    mock_filing = mock.MagicMock()
    mock_fin = mock.MagicMock()

    class FakeFilings:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, i: int) -> Any:
            return mock_filing

    with mock.patch.object(
        client,
        "_run",
        new=mock.AsyncMock(side_effect=[FakeFilings(), mock_fin]),
    ):
        result = await client.get_financials_10k("FCN", periods=1)

    assert len(result) == 1


async def test_get_financials_10k_skips_none_financials() -> None:
    """Financials.extract returning None should be skipped."""
    client = await _make_client()
    mock_filing = mock.MagicMock()

    class FakeFilings:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, i: int) -> Any:
            return mock_filing

    with mock.patch.object(
        client,
        "_run",
        new=mock.AsyncMock(side_effect=[FakeFilings(), None]),
    ):
        result = await client.get_financials_10k("FCN", periods=1)

    assert result == []


async def test_get_financials_10q_returns_list() -> None:
    client = await _make_client()
    mock_fin = mock.MagicMock()

    class FakeFilings:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, i: int) -> Any:
            return mock.MagicMock()

    with mock.patch.object(
        client,
        "_run",
        new=mock.AsyncMock(side_effect=[FakeFilings(), mock_fin]),
    ):
        result = await client.get_financials_10q("FCN", periods=1)

    assert len(result) == 1


async def test_get_recent_8k_texts() -> None:
    client = await _make_client()
    mock_filing = mock.MagicMock()
    mock_filing.document.text = "Item 1.01 Material Definitive Agreement"

    class FakeFilings:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, i: int) -> Any:
            return mock_filing

    # _run returns: first call → FakeFilings, second call → text string
    with mock.patch.object(
        client,
        "_run",
        new=mock.AsyncMock(side_effect=[FakeFilings(), "Item 1.01 Material Definitive Agreement"]),
    ):
        result = await client.get_recent_8k_texts("UNH", lookback_days=60)

    assert isinstance(result, list)


async def test_run_method_invokes_executor() -> None:
    """_run must acquire limiter and call run_in_executor."""
    client = await _make_client()

    with mock.patch.object(client._limiter, "acquire", new=mock.AsyncMock()):
        mock_loop = mock.AsyncMock()
        mock_loop.run_in_executor = mock.AsyncMock(return_value="result")
        with mock.patch("asyncio.get_running_loop", return_value=mock_loop):
            result = await client._run(lambda: "result")

    assert result == "result"
