# module-kind: adapter
"""Generic HTTP web-search provider.

A single, vendor-agnostic :class:`WebSearchProvider` implementation driven
entirely by a :class:`SearchProviderPreset`. It resolves the API key from a
bound connection at call time (never at construction, so a rotated secret is
picked up on the next search), resolves the endpoint's DNS once and pins every
(retryable) request attempt to that IP behind the injected
:class:`NetworkPolicy` (SSRF guard, redirects disabled), retries transient
failures through :class:`GeneralRetryHandler`, applies a per-connection rate
limit so an agent loop cannot cause runaway spend, and normalises the
provider's JSON into :class:`SearchResult` objects.

Credential resolution reuses the connection catalog (the same brokering the
governed external-access tool uses); nothing about the provider is
vendor-specific beyond the injected preset.
"""

import copy
from collections.abc import Mapping
from typing import Final, Protocol, runtime_checkable

import httpx
from pydantic import JsonValue

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience.general_retry import GeneralRetryHandler
from synthorg.core.resilience.retry_after import (
    coerce_finite_nonneg_seconds,
    parse_retry_after_seconds,
)
from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.core.types import require_not_blank
from synthorg.integrations.rate_limiting.decorator import with_connection_rate_limit
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_SEARCH_FAILED,
    WEB_SEARCH_RETRY,
    WEB_SEARCH_START,
)
from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    is_allowed_http_scheme,
    validate_url_host,
)
from synthorg.tools.web.errors import (
    WebSearchConfigurationError,
    WebSearchEgressBlockedError,
    WebSearchResponseError,
    WebSearchTransientError,
)
from synthorg.tools.web.providers.presets import SearchProviderPreset
from synthorg.tools.web.web_search import SearchResult

logger = get_logger(__name__)

_DEFAULT_TIMEOUT: Final[float] = 15.0
_DEFAULT_MAX_RESULTS: Final[int] = 10
_DEFAULT_RETRY_ATTEMPTS: Final[int] = 3
_DEFAULT_RETRY_BASE: Final[float] = 0.5
_DEFAULT_RETRY_CAP: Final[float] = 8.0
_HTTP_BAD_REQUEST: Final[int] = 400
_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


def _transient_delay_override(exc: Exception) -> float | None:
    """Surface a ``WebSearchTransientError``'s server-supplied cooldown.

    Returns:
        The ``retry_after_seconds`` carried on the exception, or ``None``.
    """
    return getattr(exc, "retry_after_seconds", None)


@runtime_checkable
class ConnectionCredentialSource(Protocol):
    """Minimal seam over the connection catalog's credential brokering."""

    async def get_credentials(self, name: str) -> dict[str, str]:
        """Return the decrypted credential fields for connection ``name``."""
        ...


class HttpWebSearchProvider:
    """Vendor-agnostic HTTP search provider satisfying ``WebSearchProvider``.

    Args:
        preset: The provider contract (endpoint, auth, request/response shape).
        catalog: Connection catalog resolving the bound API key at call time.
        connection_name: Name of the connection holding the provider's key.
        network_policy: SSRF policy applied to the endpoint; ``None`` uses the
            default (block private/reserved IPs).
        retry_handler: Bounded retry for transient failures; ``None`` builds a
            default exponential-backoff handler that honours a ``Retry-After``.
        timeout_seconds: Per-request timeout.
        rate_limiter: Per-connection rate-limit ceiling; ``None`` uses the
            decorator's default so a runaway agent loop still cannot exceed a
            bounded request rate against a paid provider.
        clock: Clock seam for the default retry handler's backoff.

    Raises:
        ValueError: If ``timeout_seconds`` or ``max_results_ceiling`` is not
            positive.
    """

    def __init__(  # noqa: PLR0913 -- injected collaborators + tunables
        self,
        *,
        preset: SearchProviderPreset,
        catalog: ConnectionCredentialSource,
        connection_name: str,
        network_policy: NetworkPolicy | None = None,
        retry_handler: GeneralRetryHandler | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        max_results_ceiling: int | None = None,
        rate_limiter: RateLimiterConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            msg = f"timeout_seconds must be positive, got {timeout_seconds}"
            raise ValueError(msg)
        if max_results_ceiling is not None and max_results_ceiling <= 0:
            msg = f"max_results_ceiling must be positive, got {max_results_ceiling}"
            raise ValueError(msg)
        self._preset = preset
        self._catalog = catalog
        self._connection_name = require_not_blank(connection_name, "connection_name")
        self._network_policy = (
            network_policy if network_policy is not None else NetworkPolicy()
        )
        self._timeout = timeout_seconds
        self._max_results_ceiling = max_results_ceiling
        self._rate_limiter = rate_limiter
        self._retry = (
            retry_handler
            if retry_handler is not None
            else GeneralRetryHandler(
                retryable=lambda exc: isinstance(exc, WebSearchTransientError),
                max_attempts=_DEFAULT_RETRY_ATTEMPTS,
                base=_DEFAULT_RETRY_BASE,
                cap=_DEFAULT_RETRY_CAP,
                event=WEB_SEARCH_RETRY,
                jitter=False,
                delay_override=_transient_delay_override,
                clock=clock if clock is not None else SystemClock(),
            )
        )

    async def search(
        self,
        query: str,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> list[SearchResult]:
        """Execute a search, resolving credentials and retrying transients.

        Args:
            query: The search query string.
            max_results: Requested result count (clamped to the preset cap).

        Returns:
            The normalised search results (possibly empty).

        Raises:
            WebSearchConfigurationError: If the bound connection has no key.
            WebSearchEgressBlockedError: If the endpoint fails the SSRF check.
            WebSearchResponseError: On a non-retryable upstream error.
            WebSearchTransientError: If retries are exhausted.
        """
        logger.debug(
            WEB_SEARCH_START,
            provider=self._preset.id,
            max_results=max_results,
        )
        key = await self._resolve_key()
        validation = await self._validate_endpoint()
        cap = self._preset.max_results_cap
        if self._max_results_ceiling is not None:
            cap = min(cap, self._max_results_ceiling)
        count = min(max_results, cap)

        # Each retry attempt is a real request, so the rate limit wraps the
        # per-attempt call (retries count against the ceiling), not the outer
        # retry loop. A coroutine function (not a lambda returning a coroutine)
        # so a decorator that inspects ``iscoroutinefunction`` gates the actual
        # request, not just the instantaneous coroutine creation.
        @with_connection_rate_limit(
            self._connection_name,
            config=self._rate_limiter,
        )
        async def rate_limited() -> list[SearchResult]:
            return await self._request_once(
                query=query,
                count=count,
                key=key,
                validation=validation,
            )

        return await self._retry.execute(rate_limited, provider=self._preset.id)

    async def _resolve_key(self) -> str:
        """Broker the API key from the bound connection.

        Returns:
            The resolved API key.

        Raises:
            WebSearchConfigurationError: If the connection cannot be resolved,
                or has no ``api_key`` / ``token`` / ``access_token`` field.
        """
        try:
            creds = await self._catalog.get_credentials(self._connection_name)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                WEB_SEARCH_FAILED,
                provider=self._preset.id,
                reason="credential_resolution_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Wrap so a deleted connection / secret-backend failure surfaces as
            # the module's domain error with a scrubbed message, not a raw
            # (possibly credential-bearing) exception leaking to the caller.
            msg = (
                f"could not resolve credentials for connection "
                f"{self._connection_name!r}: {safe_error_description(exc)}"
            )
            raise WebSearchConfigurationError(msg) from exc
        key = creds.get("api_key") or creds.get("token") or creds.get("access_token")
        if not key:
            logger.warning(
                WEB_SEARCH_FAILED,
                provider=self._preset.id,
                reason="missing_credential",
            )
            msg = (
                f"connection {self._connection_name!r} has no api_key/token "
                f"for web search provider {self._preset.id!r}"
            )
            raise WebSearchConfigurationError(msg)
        return key

    async def _validate_endpoint(self) -> DnsValidationOk:
        """Run the SSRF pre-flight on the preset endpoint.

        Returns:
            The DNS validation result (carrying pinnable IPs) on success.

        Raises:
            WebSearchEgressBlockedError: If the scheme or host is rejected.
        """
        endpoint = self._preset.endpoint
        if not is_allowed_http_scheme(endpoint):
            msg = f"web search endpoint scheme not allowed: {endpoint!r}"
            raise WebSearchEgressBlockedError(msg)
        result = await validate_url_host(endpoint, self._network_policy)
        if isinstance(result, str):
            raise WebSearchEgressBlockedError(result)
        return result

    async def _request_once(
        self,
        *,
        query: str,
        count: int,
        key: str,
        validation: DnsValidationOk,
    ) -> list[SearchResult]:
        """Issue one DNS-pinned request and parse the response.

        Returns:
            The parsed search results.

        Raises:
            WebSearchTransientError: On transport failure or a retryable status.
            WebSearchResponseError: On a non-retryable status or bad body.
        """
        transport: PinnedDnsTransport | None = None
        if validation.resolved_ips:
            transport = PinnedDnsTransport(
                hostname=validation.hostname,
                ip=validation.resolved_ips[0],
            )
        headers = {
            "Accept": "application/json",
            self._preset.auth_header: self._preset.auth_template.format(key=key),
        }
        try:
            async with httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
            ) as client:
                response = await self._send(
                    client,
                    query=query,
                    count=count,
                    headers=headers,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning(
                WEB_SEARCH_FAILED,
                provider=self._preset.id,
                reason="transport_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"web search transport error: {safe_error_description(exc)}"
            raise WebSearchTransientError(msg) from exc
        finally:
            if transport is not None:
                await transport.aclose()

        return self._parse_response(response, count)

    async def _send(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        count: int,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Dispatch the preset's GET or POST request.

        Returns:
            The upstream HTTP response.
        """
        if self._preset.method == "GET":
            params: dict[str, str] = {self._preset.query_key: query}
            if self._preset.count_key is not None:
                params[self._preset.count_key] = str(count)
            return await client.get(
                self._preset.endpoint,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        # Deep-copy the preset's constant extra: ``get_search_preset`` hands out
        # the shared registry instance, and ``extra`` is a mutable dict (nested,
        # so a shallow spread still aliases sub-dicts) on the frozen model.
        # Copying at this boundary keeps a per-request body from mutating the
        # shared preset used by every later request.
        body: dict[str, JsonValue] = {
            self._preset.query_key: query,
            **copy.deepcopy(self._preset.extra),
        }
        if self._preset.count_key is not None:
            body[self._preset.count_key] = count
        return await client.post(
            self._preset.endpoint,
            json=body,
            headers=headers,
            timeout=self._timeout,
        )

    def _parse_response(
        self,
        response: httpx.Response,
        count: int,
    ) -> list[SearchResult]:
        """Validate the status and extract results from the JSON body.

        Returns:
            The normalised results.

        Raises:
            WebSearchTransientError: On a retryable status.
            WebSearchResponseError: On a non-retryable status or bad JSON.
        """
        status = response.status_code
        if status in _RETRYABLE_STATUSES:
            retry_after = self._retry_after_seconds(response)
            logger.warning(
                WEB_SEARCH_FAILED,
                provider=self._preset.id,
                reason="retryable_status",
                status_code=status,
                retry_after_seconds=retry_after,
            )
            msg = f"web search provider returned status {status}"
            raise WebSearchTransientError(msg, retry_after_seconds=retry_after)
        if status >= _HTTP_BAD_REQUEST:
            logger.warning(
                WEB_SEARCH_FAILED,
                provider=self._preset.id,
                reason="error_status",
                status_code=status,
            )
            msg = f"web search provider returned status {status}"
            raise WebSearchResponseError(msg)
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning(
                WEB_SEARCH_FAILED,
                provider=self._preset.id,
                reason="malformed_json",
                error_type=type(exc).__name__,
            )
            msg = "web search provider returned a malformed JSON body"
            raise WebSearchResponseError(msg) from exc
        return self._extract_results(payload, count)

    def _retry_after_seconds(
        self,
        response: httpx.Response,
    ) -> float | None:
        """Parse a ``Retry-After`` header into seconds for any retryable status.

        Honouring the server's own cooldown avoids hammering a rate-limited or
        temporarily-unavailable provider (429, 503, ...) on the fixed
        exponential schedule; the header is advisory on every retryable status,
        not just 429.

        Returns:
            The parsed non-negative delay, or ``None`` when absent/unparseable.
        """
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        return coerce_finite_nonneg_seconds(parse_retry_after_seconds(raw))

    def _extract_results(
        self,
        payload: object,
        count: int,
    ) -> list[SearchResult]:
        """Walk the preset result path and coerce items to ``SearchResult``.

        Malformed items (missing title/url, wrong types) are dropped rather
        than failing the whole search, so one bad row cannot blank a page.

        Returns:
            The coerced results (at most ``count``).
        """
        node: object = payload
        for path_key in self._preset.results_path:
            if not isinstance(node, Mapping):
                node = None
                break
            node = node.get(path_key)
        if not isinstance(node, list):
            logger.warning(
                WEB_SEARCH_FAILED,
                provider=self._preset.id,
                reason="unexpected_response_shape",
            )
            return []
        out: list[SearchResult] = []
        for item in node[:count]:
            if not isinstance(item, Mapping):
                continue
            url = item.get(self._preset.url_key)
            title = item.get(self._preset.title_key)
            if not (isinstance(url, str) and url and isinstance(title, str) and title):
                continue
            snippet_raw = item.get(self._preset.snippet_key)
            snippet = snippet_raw if isinstance(snippet_raw, str) else ""
            out.append(SearchResult(title=title, url=url, snippet=snippet))
        return out
