# module-kind: adapter
"""The ``proxy`` fetch rung: a vendor's reader fetches the page for us.

Mirrors :class:`HttpWebSearchProvider` exactly -- credential brokered per call
so a rotated key is picked up on the next fetch, DNS pinned to the address the
SSRF check validated, redirects refused, transients retried under a bounded
handler, and the whole thing rate-limited per connection.

The target URL is checked here as well as in the tool. Under this rung the
vendor is the one opening the socket, so a target of ``169.254.169.254`` would
be fetched by them and the cloud-metadata response handed back to us: the
policy has to bind what we ASK for, not only what we connect to.
"""

import copy
import json
from collections.abc import Mapping
from typing import Final

import httpx
from pydantic import JsonValue

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience.general_retry import GeneralRetryHandler
from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.core.types import require_not_blank
from synthorg.integrations.rate_limiting.decorator import with_connection_rate_limit
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_FETCH_FAILED,
    WEB_FETCH_RETRY,
    WEB_FETCH_START,
)
from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    is_allowed_http_scheme,
    validate_url_host,
)
from synthorg.tools.web._guarded_fetch import (
    RETRYABLE_STATUSES,
    retry_after_seconds,
    stream_bounded,
)
from synthorg.tools.web.errors import (
    WebFetchConfigurationError,
    WebFetchEgressBlockedError,
    WebFetchResponseError,
    WebFetchTransientError,
)
from synthorg.tools.web.extract import extract_markdown, truncate_with_notice
from synthorg.tools.web.fetch_types import FetchBackend, FetchBudget, FetchedPage
from synthorg.tools.web.providers.fetch_presets import FetchProviderPreset
from synthorg.tools.web.providers.http_search_provider import (
    ConnectionCredentialSource,
)

logger = get_logger(__name__)

_DEFAULT_TIMEOUT: Final[float] = 30.0
_DEFAULT_RETRY_ATTEMPTS: Final[int] = 3
_DEFAULT_RETRY_BASE: Final[float] = 0.5
_DEFAULT_RETRY_CAP: Final[float] = 8.0
_HTTP_BAD_REQUEST: Final[int] = 400
_MAX_LINKS: Final[int] = 50


def _transient_delay_override(exc: Exception) -> float | None:
    """Surface a ``WebFetchTransientError``'s server-supplied cooldown.

    Returns:
        The ``retry_after_seconds`` carried on the exception, or ``None``.
    """
    return getattr(exc, "retry_after_seconds", None)


class HttpWebFetchProvider:
    """Read a page through a vendor's reader endpoint.

    Args:
        preset: The reader contract (endpoint, auth, request/response shape).
        catalog: Connection catalog resolving the bound API key at call time.
        connection_name: Name of the connection holding the vendor's key.
        budget: How much of the reader's reply this rung accepts.
        network_policy: SSRF policy applied to both the reader endpoint and
            the target URL.
        retry_handler: Bounded retry for transients; ``None`` builds a default.
        timeout_seconds: Per-request timeout.
        rate_limiter: Per-connection ceiling; ``None`` uses the decorator's
            default so a runaway loop still cannot exceed a bounded rate.
        clock: Clock seam for the default retry handler's backoff.

    Raises:
        ValueError: If a bound is not positive.
    """

    def __init__(  # noqa: PLR0913 -- injected collaborators + tunables
        self,
        *,
        preset: FetchProviderPreset,
        catalog: ConnectionCredentialSource,
        connection_name: str,
        budget: FetchBudget,
        network_policy: NetworkPolicy | None = None,
        retry_handler: GeneralRetryHandler | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        rate_limiter: RateLimiterConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            msg = f"timeout_seconds must be positive, got {timeout_seconds}"
            raise ValueError(msg)
        self._preset = preset
        self._catalog = catalog
        self._connection_name = require_not_blank(connection_name, "connection_name")
        self._char_budget = budget.char_budget
        self._max_response_bytes = budget.max_response_bytes
        self._network_policy = (
            network_policy if network_policy is not None else NetworkPolicy()
        )
        self._timeout = timeout_seconds
        self._rate_limiter = rate_limiter
        self._clock = clock if clock is not None else SystemClock()
        self._retry = (
            retry_handler
            if retry_handler is not None
            else GeneralRetryHandler(
                retryable=lambda exc: isinstance(exc, WebFetchTransientError),
                max_attempts=_DEFAULT_RETRY_ATTEMPTS,
                base=_DEFAULT_RETRY_BASE,
                cap=_DEFAULT_RETRY_CAP,
                event=WEB_FETCH_RETRY,
                jitter=False,
                delay_override=_transient_delay_override,
                clock=self._clock,
            )
        )

    @property
    def backend(self) -> FetchBackend:
        """This rung's identity."""
        return FetchBackend.PROXY

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What this vendor's reader offers beyond plain content."""
        return self._preset.capabilities

    async def fetch(self, url: str) -> FetchedPage:
        """Read *url* through the vendor's reader.

        Args:
            url: Absolute http(s) URL to read.

        Returns:
            The page as markdown; ``markdown`` is empty when the reader
            returned nothing usable.

        Raises:
            WebFetchConfigurationError: If the bound connection has no key.
            WebFetchEgressBlockedError: If the target or endpoint is rejected.
            WebFetchResponseError: On a non-retryable upstream error.
            WebFetchTransientError: If retries are exhausted.
        """
        logger.debug(WEB_FETCH_START, provider=self._preset.id, backend="proxy")
        await self._reject_disallowed_target(url)
        key = await self._resolve_key()
        validation = await self._validate_endpoint()

        @with_connection_rate_limit(
            self._connection_name,
            config=self._rate_limiter,
        )
        async def rate_limited() -> FetchedPage:
            return await self._request_once(url=url, key=key, validation=validation)

        return await self._retry.execute(rate_limited, provider=self._preset.id)

    async def _reject_disallowed_target(self, url: str) -> None:
        """Refuse a target the network policy would not let us fetch ourselves.

        The host is resolved and judged even though this rung never opens a
        socket to it: the vendor does that on our behalf, so a target of
        ``169.254.169.254`` would come back as cloud-metadata through a
        connection we pay for. There is no rebinding window to worry about
        here, because we never connect; what is being bound is what we ASK
        for. The tool checks the same URL before choosing a rung, and this
        check standing on its own is what keeps that true for any future
        caller that reaches a provider directly.

        Raises:
            WebFetchEgressBlockedError: If the scheme or host is not allowed.
        """
        if not is_allowed_http_scheme(url):
            msg = f"fetch target scheme not allowed: {url!r}"
            raise WebFetchEgressBlockedError(msg)
        outcome = await validate_url_host(url, self._network_policy)
        if isinstance(outcome, str):
            logger.warning(
                WEB_FETCH_FAILED,
                provider=self._preset.id,
                reason="target_host_blocked",
            )
            raise WebFetchEgressBlockedError(outcome)

    async def _resolve_key(self) -> str:
        """Broker the API key from the bound connection.

        Returns:
            The resolved API key.

        Raises:
            WebFetchConfigurationError: If the connection cannot be resolved,
                or has no ``api_key`` / ``token`` / ``access_token`` field.
        """
        try:
            creds = await self._catalog.get_credentials(self._connection_name)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                WEB_FETCH_FAILED,
                provider=self._preset.id,
                reason="credential_resolution_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"could not resolve credentials for connection "
                f"{self._connection_name!r}: {safe_error_description(exc)}"
            )
            raise WebFetchConfigurationError(msg) from exc
        key = creds.get("api_key") or creds.get("token") or creds.get("access_token")
        if not key:
            logger.warning(
                WEB_FETCH_FAILED,
                provider=self._preset.id,
                reason="missing_credential",
            )
            msg = (
                f"connection {self._connection_name!r} has no api_key/token "
                f"for web fetch provider {self._preset.id!r}"
            )
            raise WebFetchConfigurationError(msg)
        return key

    async def _validate_endpoint(self) -> DnsValidationOk:
        """Run the SSRF pre-flight on the reader endpoint.

        Returns:
            The DNS validation result carrying pinnable IPs.

        Raises:
            WebFetchEgressBlockedError: If the scheme or host is rejected.
        """
        endpoint = self._preset.endpoint
        if not is_allowed_http_scheme(endpoint):
            msg = f"web fetch endpoint scheme not allowed: {endpoint!r}"
            raise WebFetchEgressBlockedError(msg)
        result = await validate_url_host(endpoint, self._network_policy)
        if isinstance(result, str):
            raise WebFetchEgressBlockedError(result)
        return result

    async def _request_once(
        self,
        *,
        url: str,
        key: str,
        validation: DnsValidationOk,
    ) -> FetchedPage:
        """Issue one DNS-pinned reader request and parse the response.

        Returns:
            The parsed page.

        Raises:
            WebFetchTransientError: On transport failure or a retryable status.
            WebFetchResponseError: On a non-retryable status or bad body.
        """
        transport: PinnedDnsTransport | None = None
        if validation.resolved_ips:
            transport = PinnedDnsTransport(
                hostname=validation.hostname,
                ip=validation.resolved_ips[0],
            )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self._preset.auth_headers(key),
        }
        body: dict[str, JsonValue] = {
            self._preset.url_key: [url] if self._preset.url_as_list else url,
            **copy.deepcopy(self._preset.extra),
        }
        try:
            # Streamed under the operator's byte ceiling rather than buffered
            # whole: the reader is a third party, and ``.json()`` would read
            # whatever it sends into memory before anything gets to judge the
            # size.
            raw, status, response_headers = await stream_bounded(
                self._preset.endpoint,
                "POST",
                headers=headers,
                body=json.dumps(body, separators=(",", ":")),
                timeout=self._timeout,
                max_bytes=self._max_response_bytes,
                transport=transport,
            )
        # ``TimeoutError`` is the total-deadline breach from ``stream_bounded``,
        # raised by asyncio rather than the transport, and transient the same
        # way a per-read timeout is.
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning(
                WEB_FETCH_FAILED,
                provider=self._preset.id,
                reason="transport_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"web fetch transport error: {safe_error_description(exc)}"
            raise WebFetchTransientError(msg) from exc
        finally:
            if transport is not None:
                await transport.aclose()

        return await self._parse_response(raw, status, response_headers, url)

    async def _parse_response(
        self,
        raw: bytes,
        status: int,
        response_headers: httpx.Headers,
        url: str,
    ) -> FetchedPage:
        """Validate the status and extract the document from the JSON body.

        Returns:
            The parsed page.

        Raises:
            WebFetchTransientError: On a retryable status.
            WebFetchResponseError: On a non-retryable status or bad JSON.
        """
        if status in RETRYABLE_STATUSES:
            retry_after = retry_after_seconds(response_headers)
            logger.warning(
                WEB_FETCH_FAILED,
                provider=self._preset.id,
                reason="retryable_status",
                status_code=status,
                retry_after_seconds=retry_after,
            )
            msg = f"web fetch reader returned status {status}"
            raise WebFetchTransientError(msg, retry_after_seconds=retry_after)
        if status >= _HTTP_BAD_REQUEST:
            logger.warning(
                WEB_FETCH_FAILED,
                provider=self._preset.id,
                reason="error_status",
                status_code=status,
            )
            msg = f"web fetch reader returned status {status}"
            raise WebFetchResponseError(msg)
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            logger.warning(
                WEB_FETCH_FAILED,
                provider=self._preset.id,
                reason="malformed_json",
                error_type=type(exc).__name__,
            )
            msg = "web fetch reader returned a malformed JSON body"
            raise WebFetchResponseError(msg) from exc
        return await self._to_page(payload, url)

    async def _to_page(self, payload: object, url: str) -> FetchedPage:
        """Walk the preset's result path and build the page.

        Returns:
            The page; ``markdown`` is empty when the reader returned nothing.
        """
        document = self._locate_document(payload)
        if document is None:
            logger.warning(
                WEB_FETCH_FAILED,
                provider=self._preset.id,
                reason="unexpected_response_shape",
            )
            return FetchedPage(url=url, markdown="", backend=FetchBackend.PROXY)

        raw = document.get(self._preset.content_key)
        content = raw if isinstance(raw, str) else ""
        title = ""
        if self._preset.title_key is not None:
            candidate = document.get(self._preset.title_key)
            title = candidate.strip() if isinstance(candidate, str) else ""

        hidden_detected = False
        if self._preset.content_is_markdown:
            # A reader that already returned markdown gives us nothing to
            # strip: whatever the page hid was resolved inside the vendor,
            # which is one reason a rung that hands the page to a third party
            # is not the same trust position as reading it ourselves.
            markdown, truncated = truncate_with_notice(content, self._char_budget)
        else:
            extracted = await extract_markdown(
                content, char_budget=self._char_budget, url=url
            )
            markdown, truncated, title = (
                extracted.markdown,
                extracted.truncated,
                title or extracted.title,
            )
            hidden_detected = extracted.hidden_content_detected

        return FetchedPage(
            url=url,
            final_url=url,
            title=title,
            markdown=markdown,
            backend=FetchBackend.PROXY,
            truncated=truncated,
            links=self._links_of(document),
            hidden_content_detected=hidden_detected,
        )

    def _locate_document(self, payload: object) -> Mapping[str, object] | None:
        """Walk ``results_path`` to the document object.

        Returns:
            The document mapping, or ``None`` when the shape does not match.
        """
        node: object = payload
        for path_key in self._preset.results_path:
            if not isinstance(node, Mapping):
                return None
            node = node.get(path_key)
        if self._preset.result_is_list:
            if not isinstance(node, list) or not node:
                return None
            node = node[0]
        return node if isinstance(node, Mapping) else None

    def _links_of(self, document: Mapping[str, object]) -> tuple[str, ...]:
        """Read the document's outbound links, capped.

        Returns:
            Up to ``_MAX_LINKS`` link strings, empty when the vendor returns
            none or the field is not a list of strings.
        """
        if self._preset.links_key is None:
            return ()
        raw = document.get(self._preset.links_key)
        if not isinstance(raw, list):
            return ()
        return tuple(item for item in raw if isinstance(item, str))[:_MAX_LINKS]


__all__ = ["HttpWebFetchProvider"]
