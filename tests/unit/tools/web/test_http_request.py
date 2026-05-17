"""Unit tests for HttpRequestTool."""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import structlog

from synthorg.observability.events.web import (
    WEB_REQUEST_FAILED,
    WEB_REQUEST_START,
    WEB_REQUEST_SUCCESS,
    WEB_REQUEST_TIMEOUT,
)
from synthorg.providers.url_utils import redact_url
from synthorg.tools.network_validator import DnsValidationOk, NetworkPolicy
from synthorg.tools.web.http_request import HttpRequestTool

# Credential-bearing URL: userinfo + a secret query parameter. After
# redaction the structured-log ``url`` field must equal
# ``redact_url(...)`` and carry neither the userinfo nor the query.
_CRED_URL = "http://alice:s3cr3t@example.com/api?access_token=abc123XYZ"


class _RaisingStream:
    """Async context manager that raises on entry."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self) -> None:
        raise self._exc

    async def __aexit__(self, *_args: Any) -> None:
        pass  # pragma: no cover


def _mock_stream_client(
    response: httpx.Response | None = None,
    *,
    side_effect: Exception | None = None,
) -> AsyncMock:
    """Build an AsyncMock httpx.AsyncClient that supports ``.stream()``.

    When *side_effect* is set, ``stream()`` raises immediately.
    Otherwise it yields *response* as a streaming context manager.
    """
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    if side_effect is not None:
        client.stream = lambda **_kw: _RaisingStream(side_effect)
    elif response is not None:

        async def _aiter_bytes() -> AsyncIterator[bytes]:
            yield response.content

        response.aiter_bytes = _aiter_bytes  # type: ignore[assignment]

        @asynccontextmanager
        async def _stream(**_kwargs: object) -> AsyncIterator[httpx.Response]:
            yield response

        client.stream = _stream

    return client


class TestHttpRequestTool:
    """Tests for HTTP request execution."""

    @pytest.mark.unit
    async def test_get_request_success(self, http_tool: HttpRequestTool) -> None:
        mock_response = httpx.Response(200, content=b"hello world")
        with patch("synthorg.tools.web.http_request.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_stream_client(mock_response)

            result = await http_tool.execute(
                arguments={"url": "http://example.com/api"}
            )

        assert result.is_error is False
        assert "hello world" in result.content
        assert result.metadata["status_code"] == 200

    @pytest.mark.unit
    async def test_post_with_body(self, http_tool: HttpRequestTool) -> None:
        mock_response = httpx.Response(201, content=b"created")
        with patch("synthorg.tools.web.http_request.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_stream_client(mock_response)

            result = await http_tool.execute(
                arguments={
                    "url": "http://example.com/api",
                    "method": "POST",
                    "body": '{"key": "value"}',
                }
            )

        assert result.is_error is False
        assert result.metadata["status_code"] == 201

    @pytest.mark.unit
    async def test_timeout_returns_error(self, http_tool: HttpRequestTool) -> None:
        with patch("synthorg.tools.web.http_request.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_stream_client(
                side_effect=httpx.ReadTimeout("timed out"),
            )

            result = await http_tool.execute(
                arguments={"url": "http://example.com/slow"}
            )

        assert result.is_error is True
        assert "timed out" in result.content.lower()

    @pytest.mark.unit
    async def test_http_error_returns_error(self, http_tool: HttpRequestTool) -> None:
        with patch("synthorg.tools.web.http_request.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_stream_client(
                side_effect=httpx.ConnectError("connection refused"),
            )

            result = await http_tool.execute(
                arguments={"url": "http://example.com/down"}
            )

        assert result.is_error is True
        assert "failed" in result.content.lower()

    @pytest.mark.unit
    async def test_unsupported_method(self, http_tool: HttpRequestTool) -> None:
        result = await http_tool.execute(
            arguments={"url": "http://x.com", "method": "PATCH"}
        )
        assert result.is_error is True
        assert "unsupported" in result.content.lower()

    @pytest.mark.unit
    async def test_response_truncation(self) -> None:
        tool = HttpRequestTool(
            network_policy=NetworkPolicy(block_private_ips=False),
            max_response_bytes=50,
        )
        mock_response = httpx.Response(200, content=b"x" * 100)
        with patch("synthorg.tools.web.http_request.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_stream_client(mock_response)

            result = await tool.execute(arguments={"url": "http://example.com/big"})

        assert result.is_error is False
        assert "truncated" in result.content.lower()
        assert result.metadata["truncated"] is True


class TestSsrfPrevention:
    """Tests for SSRF prevention in HttpRequestTool."""

    @pytest.mark.unit
    async def test_private_ip_blocked(self) -> None:
        tool = HttpRequestTool()
        result = await tool.execute(arguments={"url": "http://127.0.0.1/admin"})
        assert result.is_error is True
        assert "blocked" in result.content.lower()

    @pytest.mark.unit
    async def test_file_scheme_blocked(self) -> None:
        tool = HttpRequestTool()
        result = await tool.execute(arguments={"url": "file:///etc/passwd"})
        assert result.is_error is True
        assert "scheme" in result.content.lower()

    @pytest.mark.unit
    async def test_ftp_scheme_blocked(self) -> None:
        tool = HttpRequestTool()
        result = await tool.execute(arguments={"url": "ftp://files.example.com/data"})
        assert result.is_error is True

    @pytest.mark.unit
    async def test_flag_injection_blocked(self) -> None:
        tool = HttpRequestTool()
        result = await tool.execute(arguments={"url": "-http://evil.com"})
        assert result.is_error is True


def _events(
    logs: Sequence[Mapping[str, object]], name: str
) -> list[Mapping[str, object]]:
    """Captured structlog records whose ``event`` equals *name*."""
    return [log for log in logs if log.get("event") == name]


def _assert_redacted(record: Mapping[str, object]) -> None:
    """The record's ``url`` field is redacted and leaks no credentials."""
    logged = record["url"]
    assert logged == redact_url(_CRED_URL)
    assert "s3cr3t" not in str(logged)
    assert "abc123XYZ" not in str(logged)
    assert "alice" not in str(logged)


class TestUrlRedactionInLogs:
    """The 4 ``http_request`` log sites must redact credential-bearing URLs.

    Validation is patched to a deterministic ``DnsValidationOk`` so the
    test does not perform real DNS and is not flaky on the userinfo URL.
    """

    @pytest.fixture
    def http_tool(self, http_tool: HttpRequestTool) -> HttpRequestTool:
        ok = DnsValidationOk(hostname="example.com", port=80)
        http_tool._validate_url = AsyncMock(return_value=ok)  # type: ignore[method-assign]
        return http_tool

    @pytest.mark.unit
    async def test_start_and_success_redact_url(
        self, http_tool: HttpRequestTool
    ) -> None:
        mock_response = httpx.Response(200, content=b"ok")
        with (
            patch("synthorg.tools.web.http_request.httpx.AsyncClient") as mock_cls,
            structlog.testing.capture_logs() as logs,
        ):
            mock_cls.return_value = _mock_stream_client(mock_response)
            result = await http_tool.execute(arguments={"url": _CRED_URL})

        assert result.is_error is False
        starts = _events(logs, WEB_REQUEST_START)
        successes = _events(logs, WEB_REQUEST_SUCCESS)
        assert len(starts) == 1
        assert len(successes) == 1
        _assert_redacted(starts[0])
        _assert_redacted(successes[0])

    @pytest.mark.unit
    async def test_timeout_redacts_url(self, http_tool: HttpRequestTool) -> None:
        with (
            patch("synthorg.tools.web.http_request.httpx.AsyncClient") as mock_cls,
            structlog.testing.capture_logs() as logs,
        ):
            mock_cls.return_value = _mock_stream_client(
                side_effect=httpx.ReadTimeout("timed out"),
            )
            result = await http_tool.execute(arguments={"url": _CRED_URL})

        assert result.is_error is True
        timeouts = _events(logs, WEB_REQUEST_TIMEOUT)
        assert len(timeouts) == 1
        _assert_redacted(timeouts[0])

    @pytest.mark.unit
    async def test_failed_redacts_url(self, http_tool: HttpRequestTool) -> None:
        with (
            patch("synthorg.tools.web.http_request.httpx.AsyncClient") as mock_cls,
            structlog.testing.capture_logs() as logs,
        ):
            mock_cls.return_value = _mock_stream_client(
                side_effect=httpx.ConnectError("connection refused"),
            )
            result = await http_tool.execute(arguments={"url": _CRED_URL})

        assert result.is_error is True
        failures = _events(logs, WEB_REQUEST_FAILED)
        assert len(failures) == 1
        _assert_redacted(failures[0])
