"""HTTP request tool -- execute HTTP requests with SSRF prevention.

Supports GET, POST, PUT, and DELETE methods.  URLs are validated
against the ``NetworkPolicy`` before requests are made.  Response
bodies are streamed and truncated at ``max_response_bytes`` to
prevent memory exhaustion.
"""

from typing import Any, ClassVar, Final

import httpx
from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_REQUEST_FAILED,
    WEB_REQUEST_START,
    WEB_REQUEST_SUCCESS,
    WEB_REQUEST_TIMEOUT,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.network_validator import (  # noqa: TC001
    DnsValidationOk,
    NetworkPolicy,
)
from synthorg.tools.web._args import HttpRequestArgs
from synthorg.tools.web.base_web_tool import BaseWebTool

logger = get_logger(__name__)

_ALLOWED_METHODS: Final[frozenset[str]] = frozenset(
    {
        "GET",
        "POST",
        "PUT",
        "DELETE",
    }
)


class HttpRequestTool(BaseWebTool):
    """Execute HTTP requests (GET/POST/PUT/DELETE).

    Validates URLs against the network policy before making requests
    to prevent SSRF attacks.  Response bodies are truncated at
    ``max_response_bytes``.

    Examples:
        Make a GET request::

            tool = HttpRequestTool()
            result = await tool.execute(
                arguments={"url": "https://api.example.com/data"}
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = HttpRequestArgs

    def __init__(
        self,
        *,
        network_policy: NetworkPolicy | None = None,
        max_response_bytes: int = 1_048_576,
        request_timeout: float = 30.0,
    ) -> None:
        """Initialize the HTTP request tool.

        Args:
            network_policy: SSRF + scheme allowlist applied to every
                outgoing request URL. ``None`` uses the default
                conservative policy.
            max_response_bytes: Hard cap on body size in bytes
                (default 1 MiB) to bound memory.
            request_timeout: Per-request timeout in seconds
                (default 30.0).
        """
        super().__init__(
            name="http_request",
            description=(
                "Execute HTTP requests (GET, POST, PUT, DELETE). "
                "URLs are validated against SSRF policies."
            ),
            parameters_schema=HttpRequestArgs.model_json_schema(),
            action_type=ActionType.COMMS_EXTERNAL,
            network_policy=network_policy,
            request_timeout=request_timeout,
        )
        self._max_response_bytes = max_response_bytes

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute an HTTP request.

        Args:
            arguments: Must contain ``url``; optionally ``method``,
                ``headers``, ``body``, ``timeout``.

        Returns:
            A ``ToolExecutionResult`` with the response body or error.
        """
        url: str = arguments["url"]
        method: str = arguments.get("method", "GET").upper()
        headers: dict[str, str] = arguments.get("headers") or {}
        body: str | None = arguments.get("body")
        raw_timeout = arguments.get("timeout")
        timeout: float = (
            raw_timeout if raw_timeout is not None else self._request_timeout
        )

        if method not in _ALLOWED_METHODS:
            return ToolExecutionResult(
                content=f"Unsupported HTTP method: {method!r}",
                is_error=True,
            )

        # SSRF validation (validate_url_host already logs WEB_SSRF_BLOCKED)
        validation = await self._validate_url(url)
        if isinstance(validation, str):
            return ToolExecutionResult(
                content=f"URL blocked: {validation}",
                is_error=True,
            )

        logger.info(
            WEB_REQUEST_START,
            method=method,
            url=url,
            has_body=body is not None,
        )

        return await self._perform_request(
            url, method, headers, body, timeout, validation
        )

    async def _perform_request(  # noqa: PLR0913
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: str | None,
        timeout: float,  # noqa: ASYNC109  -- passed to httpx, not asyncio
        validation: DnsValidationOk,
    ) -> ToolExecutionResult:
        """Perform the HTTP request after validation.

        For HTTP requests with resolved IPs, rewrites the URL to
        connect directly to the validated IP (closing the DNS
        rebinding TOCTOU gap).  For HTTPS, DNS is re-resolved by
        the TLS layer (SNI requires the hostname), so the TOCTOU
        window remains but is mitigated by the pre-request check.

        Response bodies are truncated at ``_max_response_bytes``
        (measured in bytes, not characters) to prevent memory
        exhaustion.

        Args:
            url: Validated URL.
            method: HTTP method.
            headers: Request headers.
            body: Request body.
            timeout: Request timeout.
            validation: DNS validation result carrying resolved IPs.

        Returns:
            A ``ToolExecutionResult`` with the response.
        """
        request_url, pinned_headers = self._pin_url(url, headers, validation)
        try:
            raw_bytes, status_code, resp_headers = await self._stream_response(
                request_url, method, pinned_headers, body, timeout
            )
        except httpx.TimeoutException:
            logger.warning(WEB_REQUEST_TIMEOUT, url=url, timeout=timeout)
            return ToolExecutionResult(
                content=f"Request timed out after {timeout}s: {url}",
                is_error=True,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                WEB_REQUEST_FAILED,
                url=url,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"HTTP request failed: {exc}",
                is_error=True,
            )

        truncated = len(raw_bytes) > self._max_response_bytes
        if truncated:
            raw_bytes = raw_bytes[: self._max_response_bytes]
        content = raw_bytes.decode("utf-8", errors="replace")
        content_length = len(raw_bytes)

        logger.info(
            WEB_REQUEST_SUCCESS,
            url=url,
            method=method,
            status_code=status_code,
            content_length=content_length,
            truncated=truncated,
        )

        if truncated:
            content += (
                f"\n\n[Truncated: response exceeded {self._max_response_bytes:,} bytes]"
            )

        return ToolExecutionResult(
            content=content,
            metadata={
                "status_code": status_code,
                "headers": dict(resp_headers),
                "truncated": truncated,
                "url": url,
            },
        )

    async def _stream_response(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: str | None,
        timeout: float,  # noqa: ASYNC109  -- passed to httpx, not asyncio
    ) -> tuple[bytes, int, httpx.Headers]:
        """Stream an HTTP response, reading at most ``_max_response_bytes + 1``.

        Returns ``(raw_bytes, status_code, headers)``.  Reading one
        extra byte lets the caller detect truncation without
        buffering the entire body.
        """
        # Read limit + 1 to detect truncation.
        budget = self._max_response_bytes + 1
        async with (
            httpx.AsyncClient() as client,
            client.stream(
                method=method,
                url=url,
                headers=headers,
                content=body,
                timeout=timeout,
                follow_redirects=False,
            ) as response,
        ):
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= budget:
                    break
            status_code = response.status_code
            resp_headers = response.headers
        return b"".join(chunks)[:budget], status_code, resp_headers

    @staticmethod
    def _pin_url(
        url: str,
        headers: dict[str, str],
        validation: DnsValidationOk,
    ) -> tuple[str, dict[str, str]]:
        """Rewrite URL to connect to the validated IP (HTTP only).

        For plain HTTP, replaces the hostname with the first
        validated IP and sets the ``Host`` header, closing the DNS
        rebinding TOCTOU gap.  For HTTPS, returns the original URL
        (TLS SNI requires the hostname for certificate validation).

        Returns a **(url, headers)** tuple.  The headers dict is
        copied before mutation to avoid mutating the caller's input.
        """
        from urllib.parse import urlparse  # noqa: PLC0415

        parsed = urlparse(url)

        # Always normalize Host header (case-insensitive dedup).
        normalized_headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        normalized_headers["Host"] = parsed.hostname or ""

        if not validation.resolved_ips or validation.is_https:
            return url, normalized_headers

        from ipaddress import IPv6Address, ip_address  # noqa: PLC0415
        from urllib.parse import urlunparse  # noqa: PLC0415

        pinned_ip = validation.resolved_ips[0]
        port_suffix = f":{parsed.port}" if parsed.port else ""

        # Bracket IPv6 literals in the netloc.
        try:
            addr = ip_address(pinned_ip)
        except ValueError:
            return url, normalized_headers
        if isinstance(addr, IPv6Address):
            pinned_netloc = f"[{pinned_ip}]{port_suffix}"
        else:
            pinned_netloc = f"{pinned_ip}{port_suffix}"

        return (
            urlunparse(parsed._replace(netloc=pinned_netloc)),
            normalized_headers,
        )
