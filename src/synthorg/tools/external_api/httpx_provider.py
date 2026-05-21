"""Default httpx-based ExternalAccessProvider.

Makes DNS-pinned, redirect-free requests via httpx, streaming the body up to
a hard byte budget. Transport-level failures surface as
:class:`ExternalApiResponseError`; HTTP responses (any status) are returned so
the agent can react to API-level outcomes.
"""

import httpx

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.external_api import (
    EXTERNAL_API_CALL_FAILED,
)
from synthorg.providers.url_utils import redact_url
from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.external_api.errors import ExternalApiResponseError
from synthorg.tools.external_api.provider import (
    ExternalAccessRequest,
    ExternalAccessResponse,
)

logger = get_logger(__name__)


class HttpxExternalAccessProvider:
    """httpx implementation of :class:`ExternalAccessProvider`.

    Stateless: per-request timeout and byte budget arrive on the
    :class:`ExternalAccessRequest`, so a single instance is safe to share.
    """

    async def request(
        self,
        req: ExternalAccessRequest,
    ) -> ExternalAccessResponse:
        """Stream *req* via httpx with optional DNS pinning.

        Reads at most ``max_response_bytes + 1`` to detect truncation without
        buffering an unbounded body. Never logs headers or body.
        """
        transport: httpx.AsyncBaseTransport | None = None
        if req.pinned_ip is not None and req.pinned_hostname is not None:
            transport = PinnedDnsTransport(
                hostname=req.pinned_hostname,
                ip=req.pinned_ip,
            )
        budget = req.max_response_bytes + 1
        try:
            async with (
                httpx.AsyncClient(
                    transport=transport,
                    follow_redirects=False,
                ) as client,
                client.stream(
                    method=req.method,
                    url=req.url,
                    headers=req.headers,
                    content=req.body,
                    timeout=req.timeout_seconds,
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
                resp_headers = dict(response.headers)
        except httpx.HTTPError as exc:
            logger.warning(
                EXTERNAL_API_CALL_FAILED,
                url=redact_url(req.url),
                method=req.method,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"External API request failed: {safe_error_description(exc)}"
            raise ExternalApiResponseError(msg) from exc
        finally:
            if transport is not None:
                await transport.aclose()

        raw = b"".join(chunks)
        truncated = len(raw) > req.max_response_bytes
        if truncated:
            raw = raw[: req.max_response_bytes]
        return ExternalAccessResponse(
            status_code=status_code,
            headers=resp_headers,
            body=raw.decode("utf-8", errors="replace"),
            truncated=truncated,
        )
