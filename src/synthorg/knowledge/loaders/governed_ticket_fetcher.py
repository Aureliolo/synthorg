"""Governed ticket fetcher over the external-access egress boundary.

Concrete :class:`TicketFetcher` that pulls a ticket thread through the same
SSRF-validated, DNS-pinned egress path the external-API tool uses, so a
malicious ``ticket_uri`` cannot reach the host's internal network. The
governed endpoint is expected to return the canonical ticket JSON
(``{"ticket_id": ..., "comments": [{"comment_id": ..., "body": ...}]}``);
any deviation, transport failure, or non-2xx status raises
:class:`KnowledgeIngestError`, which the loader maps to a failed source row.
"""

import json
from typing import Final

from pydantic import TypeAdapter, ValidationError

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.knowledge.errors import KnowledgeIngestError
from synthorg.knowledge.loaders.ticket import TicketThread
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_TICKET_FETCH_BLOCKED,
    KNOWLEDGE_TICKET_FETCHED,
)
from synthorg.providers.url_utils import redact_url
from synthorg.tools.external_api.provider import (
    ExternalAccessProvider,
    ExternalAccessRequest,
)
from synthorg.tools.network_validator import NetworkPolicy, validate_url_host

logger = get_logger(__name__)

_GET: Final[str] = "GET"
_HTTP_OK_MIN: Final[int] = 200
_HTTP_OK_MAX: Final[int] = 300
_THREAD_ADAPTER: Final[TypeAdapter[TicketThread]] = TypeAdapter(TicketThread)


class GovernedTicketFetcher:
    """Fetches a ticket thread through the governed egress provider.

    Args:
        provider: The egress provider that performs the DNS-pinned HTTP
            call (credentials, if any, are injected upstream by the
            governed access tool; this fetcher sends no credentials of its
            own).
        policy: SSRF network policy used to validate + resolve the ticket
            host before the request, so the connection pins the validated
            IP (closing the rebinding window).
        timeout_seconds: Per-request timeout.
        max_response_bytes: Hard cap on the response body size.
    """

    __slots__ = ("_max_response_bytes", "_policy", "_provider", "_timeout_seconds")

    def __init__(
        self,
        *,
        provider: ExternalAccessProvider,
        policy: NetworkPolicy,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def fetch(self, ticket_uri: str) -> TicketThread:
        """Return the ticket thread at ``ticket_uri`` via governed egress.

        Args:
            ticket_uri: The ticket URI to fetch.

        Returns:
            The parsed :class:`TicketThread`.

        Raises:
            KnowledgeIngestError: When the host is SSRF-blocked, the
                transport fails, the upstream returns a non-2xx status, or
                the body is not the canonical ticket JSON.
        """
        validation = await validate_url_host(ticket_uri, self._policy)
        if isinstance(validation, str):
            logger.warning(
                KNOWLEDGE_TICKET_FETCH_BLOCKED,
                url=redact_url(ticket_uri),
                reason=validation,
            )
            msg = "Ticket host blocked by SSRF policy"
            raise KnowledgeIngestError(msg)

        pinned_ip = validation.resolved_ips[0] if validation.resolved_ips else None
        pinned_hostname = validation.hostname if validation.resolved_ips else None
        request = ExternalAccessRequest(
            method=_GET,
            url=ticket_uri,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            pinned_ip=pinned_ip,
            pinned_hostname=pinned_hostname,
        )
        try:
            response = await self._provider.request(request)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                KNOWLEDGE_TICKET_FETCH_BLOCKED,
                url=redact_url(ticket_uri),
                reason="transport_failure",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Ticket fetch transport failure"
            raise KnowledgeIngestError(msg) from exc

        if not _HTTP_OK_MIN <= response.status_code < _HTTP_OK_MAX:
            logger.warning(
                KNOWLEDGE_TICKET_FETCH_BLOCKED,
                url=redact_url(ticket_uri),
                status=response.status_code,
                reason="non_2xx_status",
            )
            msg = f"Ticket endpoint returned status {response.status_code}"
            raise KnowledgeIngestError(msg)

        try:
            payload = json.loads(response.body)
            thread = parse_typed("knowledge.ticket", payload, _THREAD_ADAPTER)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            logger.warning(
                KNOWLEDGE_TICKET_FETCH_BLOCKED,
                url=redact_url(ticket_uri),
                reason="malformed_ticket_payload",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Ticket endpoint returned a malformed ticket payload"
            raise KnowledgeIngestError(msg) from exc

        logger.info(
            KNOWLEDGE_TICKET_FETCHED,
            ticket_id=str(thread.ticket_id),
            comment_count=len(thread.comments),
            truncated=response.truncated,
        )
        return thread
