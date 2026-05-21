"""Unit tests for the httpx ExternalAccessProvider."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from synthorg.tools.external_api.errors import ExternalApiResponseError
from synthorg.tools.external_api.httpx_provider import HttpxExternalAccessProvider
from synthorg.tools.external_api.provider import ExternalAccessRequest


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
    """Build an AsyncMock ``httpx.AsyncClient`` supporting ``.stream()``."""
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


def _request(*, max_response_bytes: int = 1_048_576) -> ExternalAccessRequest:
    return ExternalAccessRequest(
        method="GET",
        url="https://api.example.com/data",
        headers={"Authorization": "Bearer redacted"},
        body=None,
        timeout_seconds=30.0,
        max_response_bytes=max_response_bytes,
    )


@pytest.mark.unit
class TestExternalAccessRequestValidation:
    def test_pinned_ip_without_hostname_rejected(self) -> None:
        with pytest.raises(ValueError, match="pinned_ip and pinned_hostname"):
            ExternalAccessRequest(
                method="GET",
                url="https://api.example.com/x",
                timeout_seconds=30.0,
                max_response_bytes=1024,
                pinned_ip="203.0.113.5",
            )

    def test_pinned_hostname_without_ip_rejected(self) -> None:
        with pytest.raises(ValueError, match="pinned_ip and pinned_hostname"):
            ExternalAccessRequest(
                method="GET",
                url="https://api.example.com/x",
                timeout_seconds=30.0,
                max_response_bytes=1024,
                pinned_hostname="api.example.com",
            )

    def test_both_pinned_accepted(self) -> None:
        req = ExternalAccessRequest(
            method="GET",
            url="https://api.example.com/x",
            timeout_seconds=30.0,
            max_response_bytes=1024,
            pinned_ip="203.0.113.5",
            pinned_hostname="api.example.com",
        )
        assert req.pinned_ip == "203.0.113.5"


@pytest.mark.unit
class TestHttpxExternalAccessProvider:
    async def test_returns_response(self) -> None:
        provider = HttpxExternalAccessProvider()
        mock_response = httpx.Response(200, content=b"hello")
        with patch(
            "synthorg.tools.external_api.httpx_provider.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value = _mock_stream_client(mock_response)
            result = await provider.request(_request())
        assert result.status_code == 200
        assert result.body == "hello"
        assert result.truncated is False

    async def test_returns_4xx_5xx_without_raising(self) -> None:
        # HTTP responses (any status) are data, not transport failures.
        provider = HttpxExternalAccessProvider()
        mock_response = httpx.Response(503, content=b"upstream down")
        with patch(
            "synthorg.tools.external_api.httpx_provider.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value = _mock_stream_client(mock_response)
            result = await provider.request(_request())
        assert result.status_code == 503
        assert "upstream down" in result.body

    async def test_truncates_oversized_body(self) -> None:
        provider = HttpxExternalAccessProvider()
        mock_response = httpx.Response(200, content=b"abcdefghij")
        with patch(
            "synthorg.tools.external_api.httpx_provider.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value = _mock_stream_client(mock_response)
            result = await provider.request(_request(max_response_bytes=4))
        assert result.truncated is True
        assert result.body == "abcd"

    async def test_transport_failure_raises_response_error(self) -> None:
        provider = HttpxExternalAccessProvider()
        with patch(
            "synthorg.tools.external_api.httpx_provider.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value = _mock_stream_client(
                side_effect=httpx.ConnectError("refused"),
            )
            with pytest.raises(ExternalApiResponseError):
                await provider.request(_request())

    async def test_timeout_raises_response_error(self) -> None:
        provider = HttpxExternalAccessProvider()
        with patch(
            "synthorg.tools.external_api.httpx_provider.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value = _mock_stream_client(
                side_effect=httpx.TimeoutException("timed out"),
            )
            with pytest.raises(ExternalApiResponseError):
                await provider.request(_request())
