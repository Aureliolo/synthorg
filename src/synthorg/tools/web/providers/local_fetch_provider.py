# module-kind: adapter
"""The ``local`` fetch rung: fetch here, extract here, pay nobody.

Reuses the guarded-GET primitives ``http_request`` runs on, so the byte
ceiling, the redirect refusal and the DNS pinning are literally the same code
rather than a second implementation of the same intent. The tool has already
applied the network policy to the target before dispatch; this validates again
immediately before opening the socket, which is where the rebinding window
actually is.
"""

from typing import Final

import httpx

from synthorg.core.types import require_not_blank
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import WEB_FETCH_FAILED
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    is_allowed_http_scheme,
    validate_url_host,
)
from synthorg.tools.web._guarded_fetch import (
    RETRYABLE_STATUSES,
    decode_body,
    pin_url,
    retry_after_seconds,
    stream_bounded,
)
from synthorg.tools.web.errors import (
    WebFetchEgressBlockedError,
    WebFetchResponseError,
    WebFetchTransientError,
)
from synthorg.tools.web.extract import extract_markdown
from synthorg.tools.web.fetch_types import FetchBackend, FetchBudget, FetchedPage

logger = get_logger(__name__)

_HTTP_BAD_REQUEST: Final[int] = 400
_HTTP_MULTIPLE_CHOICES: Final[int] = 300

#: Ceiling on a cooldown this rung will repeat from an origin. Deliberately
#: not a setting: raising it is never something an operator wants, because the
#: number is chosen by whatever site the agent was pointed at. Generous enough
#: to carry a real rate-limit window, short enough that the worst a hostile
#: origin buys is one minute of one tool call.
_MAX_ORIGIN_COOLDOWN_SECONDS: Final[float] = 60.0
_ACCEPT_HEADER: Final[str] = (
    "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8"
)


class LocalFetchProvider:
    """Fetch and extract in-process, with no third party involved.

    Args:
        network_policy: SSRF policy applied immediately before connecting.
        budget: How much of the target's response this rung accepts.
        timeout_seconds: Per-request timeout.
        user_agent: Value sent as ``User-Agent``. Servers vary their response
            by it, so it is operator-visible rather than hidden.

    Raises:
        ValueError: If a bound is not positive.
    """

    def __init__(
        self,
        *,
        network_policy: NetworkPolicy | None = None,
        budget: FetchBudget,
        timeout_seconds: float,
        user_agent: str,
    ) -> None:
        if timeout_seconds <= 0:
            msg = f"timeout_seconds must be positive, got {timeout_seconds}"
            raise ValueError(msg)
        self._network_policy = (
            network_policy if network_policy is not None else NetworkPolicy()
        )
        self._max_response_bytes = budget.max_response_bytes
        self._char_budget = budget.char_budget
        self._timeout = timeout_seconds
        self._user_agent = require_not_blank(user_agent, "user_agent")

    @property
    def backend(self) -> FetchBackend:
        """This rung's identity."""
        return FetchBackend.LOCAL

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Nothing beyond markdown; the point of this rung is that it is free."""
        return ()

    async def fetch(self, url: str) -> FetchedPage:
        """Read *url* and extract it to markdown.

        Args:
            url: Absolute http(s) URL, already policy-checked by the tool.

        Returns:
            The extracted page, whose ``markdown`` is empty when the document
            carried no readable main content.

        Raises:
            WebFetchEgressBlockedError: If the scheme or host is rejected.
            WebFetchTransientError: On timeout or transport failure.
            WebFetchResponseError: On a non-2xx status.
        """
        if not is_allowed_http_scheme(url):
            msg = f"fetch target scheme not allowed: {url!r}"
            raise WebFetchEgressBlockedError(msg)
        validation = await validate_url_host(url, self._network_policy)
        if isinstance(validation, str):
            raise WebFetchEgressBlockedError(validation)

        raw, status, response_headers = await self._read(url, validation)
        self._reject_bad_status(status, response_headers)
        document = await extract_markdown(
            decode_body(raw, response_headers),
            char_budget=self._char_budget,
            url=url,
        )
        return FetchedPage(
            url=url,
            final_url=url,
            title=document.title,
            markdown=document.markdown,
            backend=FetchBackend.LOCAL,
            truncated=document.truncated,
            hidden_content_detected=document.hidden_content_detected,
        )

    async def _read(
        self,
        url: str,
        validation: DnsValidationOk,
    ) -> tuple[bytes, int, httpx.Headers]:
        """Issue the DNS-pinned bounded GET.

        Returns:
            The body bytes (capped), the status code, and the response headers.

        Raises:
            WebFetchTransientError: On timeout or transport failure.
        """
        request_url, headers = pin_url(
            url,
            {"Accept": _ACCEPT_HEADER, "User-Agent": self._user_agent},
            validation,
        )
        try:
            return await stream_bounded(
                request_url,
                "GET",
                headers=headers,
                body=None,
                timeout=self._timeout,
                max_bytes=self._max_response_bytes,
                validation=validation,
            )
        # ``TimeoutError`` is the total-deadline breach from ``stream_bounded``,
        # which httpx's own exceptions do not cover: it is raised by asyncio,
        # not by the transport, and it means the same thing to a caller.
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning(
                WEB_FETCH_FAILED,
                backend=FetchBackend.LOCAL.value,
                reason="transport_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"web fetch transport error: {safe_error_description(exc)}"
            raise WebFetchTransientError(msg) from exc

    @staticmethod
    def _reject_bad_status(status: int, response_headers: httpx.Headers) -> None:
        """Refuse every status this rung cannot extract a page from.

        Raises:
            WebFetchTransientError: On 429 or 5xx, which say "ask again", not
                "this page is unreadable". Classifying an ordinary upstream
                outage as a response error puts the agent on the next rung
                instead, which pays a vendor for a page that would have
                answered on retry. The origin's own cooldown rides along,
                CLAMPED: this rung's ``Retry-After`` comes from whatever site
                the agent was told to read, not from a vendor under contract
                like the proxy rung's, and a handler that honours an override
                honours it past its own cap by design, so an unclamped
                ``Retry-After: 86400`` would be a day-long sleep an arbitrary
                origin chose.
            WebFetchResponseError: On any other error status, and on a
                redirect.
        """
        if status in RETRYABLE_STATUSES:
            asked = retry_after_seconds(response_headers)
            retry_after = (
                min(asked, _MAX_ORIGIN_COOLDOWN_SECONDS) if asked is not None else None
            )
            logger.warning(
                WEB_FETCH_FAILED,
                backend=FetchBackend.LOCAL.value,
                reason="retryable_status",
                status_code=status,
                retry_after_seconds=retry_after,
            )
            msg = f"web fetch target returned status {status}"
            raise WebFetchTransientError(msg, retry_after_seconds=retry_after)
        if status >= _HTTP_BAD_REQUEST:
            logger.warning(
                WEB_FETCH_FAILED,
                backend=FetchBackend.LOCAL.value,
                reason="error_status",
                status_code=status,
            )
            msg = f"web fetch target returned status {status}"
            raise WebFetchResponseError(msg)
        if status >= _HTTP_MULTIPLE_CHOICES:
            # Redirects are not followed, because each hop is a new target
            # that has to clear the SSRF check on its own rather than inherit
            # the first one's verdict. A 3xx body is the origin's short "moved"
            # stub, so extracting it would report an empty page as a success;
            # naming the destination instead lets the agent re-issue against
            # it, which sends the new host through the check.
            location = response_headers.get("location", "")
            logger.warning(
                WEB_FETCH_FAILED,
                backend=FetchBackend.LOCAL.value,
                reason="redirect_not_followed",
                status_code=status,
            )
            destination = f" to {location!r}" if location else ""
            msg = (
                f"web fetch target redirected ({status}){destination};"
                " redirects are not followed, so fetch the destination"
                " directly"
            )
            raise WebFetchResponseError(msg)


__all__ = ["LocalFetchProvider"]
