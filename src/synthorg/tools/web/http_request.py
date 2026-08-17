"""HTTP request tool -- execute HTTP requests with SSRF prevention.

Supports GET, POST, PUT, and DELETE methods.  URLs are validated
against the ``NetworkPolicy`` before requests are made.  Response
bodies are streamed and truncated at ``max_response_bytes`` to
prevent memory exhaustion.
"""

from typing import ClassVar, Final, override

import httpx
from pydantic import BaseModel

from synthorg.core.boundary import parse_typed
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_REQUEST_FAILED,
    WEB_REQUEST_START,
    WEB_REQUEST_SUCCESS,
    WEB_REQUEST_TIMEOUT,
)
from synthorg.providers.url_utils import redact_url
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
)
from synthorg.tools.web._args import HttpRequestArgs
from synthorg.tools.web._guarded_fetch import pin_url, stream_bounded
from synthorg.tools.web.base_web_tool import BaseWebTool

logger = get_logger(__name__)
_DEFAULT_MAX_RESPONSE_BYTES: Final[int] = 1048576
_DEFAULT_REQUEST_TIMEOUT: Final[float] = 30.0


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
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT,
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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute an HTTP request.

        Args:
            arguments: Must contain ``url``; optionally ``method``,
                ``headers``, ``body``, ``timeout``.

        Returns:
            A ``ToolExecutionResult`` with the response body or error.
        """
        args = parse_typed("tool.execute", arguments, HttpRequestArgs)
        url = args.url
        method = args.method
        headers = args.headers
        body = args.body
        timeout: float = (
            args.timeout if args.timeout is not None else self._request_timeout
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
            url=redact_url(url),
            has_body=body is not None,
        )

        return await self._perform_request(
            url,
            method,
            headers=headers,
            body=body,
            timeout=timeout,
            validation=validation,
        )

    async def _perform_request(
        self,
        url: str,
        method: str,
        *,
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
                request_url,
                method,
                pinned_headers,
                body=body,
                timeout=timeout,
                validation=validation,
            )
        # ``TimeoutError`` is the total-deadline breach raised by asyncio
        # inside the guarded read, which httpx's own timeout type does not
        # cover but which the caller learns the same thing from.
        except TimeoutError, httpx.TimeoutException:
            logger.warning(WEB_REQUEST_TIMEOUT, url=redact_url(url), timeout=timeout)
            return ToolExecutionResult(
                content=f"Request timed out after {timeout}s: {redact_url(url)}",
                is_error=True,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                WEB_REQUEST_FAILED,
                url=redact_url(url),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"HTTP request failed: {safe_error_description(exc)}",
                is_error=True,
            )

        truncated = len(raw_bytes) > self._max_response_bytes
        if truncated:
            raw_bytes = raw_bytes[: self._max_response_bytes]
        content = raw_bytes.decode("utf-8", errors="replace")
        content_length = len(raw_bytes)

        logger.info(
            WEB_REQUEST_SUCCESS,
            url=redact_url(url),
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
        *,
        body: str | None,
        timeout: float,  # noqa: ASYNC109  -- passed to httpx, not asyncio
        validation: DnsValidationOk,
    ) -> tuple[bytes, int, httpx.Headers]:
        """Stream an HTTP response, reading at most ``_max_response_bytes + 1``.

        Returns:
            Tuple ``(bytes, int, httpx.Headers)``.
        """
        return await stream_bounded(
            url,
            method,
            headers=headers,
            body=body,
            timeout=timeout,
            max_bytes=self._max_response_bytes,
            validation=validation,
        )

    @staticmethod
    def _pin_url(
        url: str,
        headers: dict[str, str],
        validation: DnsValidationOk,
    ) -> tuple[str, dict[str, str]]:
        """Rewrite URL to connect to the validated IP (HTTP only).

        Returns:
            Tuple ``(str, dict[str, str])``.
        """
        return pin_url(url, headers, validation)
