"""Receive-path helpers for the webhooks ingest controller.

Pure helper module: connection lookup, streaming payload-size
enforcement, signature verification, timestamp parsing,
replay/freshness guarding, the bus-publish-and-log primitive, and the
durable-idempotency wrapper. The ingest controller imports these as
bare names (so tests patch them on the ingest module); the retry path
reaches ``_publish_webhook_event_and_log`` module-qualified through
this module so there is one canonical patch target.
"""

from typing import TYPE_CHECKING

from litestar import Request
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.api_core_state import idempotency_service_of
from synthorg.api.controllers._webhooks_wiring import (
    _build_idem_key,
    _build_idem_scope,
    _get_replay_protector,
)
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.integrations.webhooks.event_bus_bridge import publish_webhook_event
from synthorg.integrations.webhooks.replay_protection import MAX_NONCE_CHARS
from synthorg.integrations.webhooks.verifiers.factory import get_verifier
from synthorg.observability import get_logger
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT
from synthorg.observability.events.integrations import (
    WEBHOOK_ACCEPTED,
    WEBHOOK_REJECTED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.integrations.connections.models import Connection, ConnectionType

logger = get_logger(__name__)


async def _get_connection_or_404(state: State, connection_name: str) -> Connection:
    """Look up the named connection or raise 404 with a logged reason.

    Returns:
        ``Connection`` instance.

    Raises:
        NotFoundError: Raised on the corresponding failure path.
    """
    catalog: ConnectionCatalog = require_service(
        state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
        "Connection Catalog",
    )
    conn = await catalog.get(connection_name)
    if conn is None:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="connection not found",
        )
        msg = f"Connection '{connection_name}' not found"
        raise NotFoundError(msg)
    return conn


async def _enforce_max_payload(
    request: Request[object, object, State],
    *,
    connection_name: str,
    max_payload: int,
) -> bytes:
    """Reject oversized webhook payloads via streaming size enforcement.

    The Content-Length header is honoured up front when present, but
    a missing or understated header MUST NOT cause us to buffer an
    unbounded body before checking length: an attacker can post a
    multi-gigabyte chunked body and the worker would allocate the
    whole thing before ``request.body()`` returned. Read via
    ``request.stream()`` and abort as soon as the running total
    exceeds ``max_payload``.

    Raises :class:`ValidationError` with a structured WARNING when
    the cap is exceeded; returns the assembled body otherwise.

    Returns:
        Resulting byte string.

    Raises:
        ValidationError: Raised on the corresponding failure path.
    """
    content_length_header = request.headers.get(
        "content-length",
    ) or request.headers.get("Content-Length")
    if content_length_header:
        try:
            content_length = int(content_length_header)
        except ValueError:
            logger.warning(
                WEBHOOK_REJECTED,
                connection_name=connection_name,
                reason="malformed content-length header",
            )
            msg = "Malformed Content-Length header"
            raise ValidationError(msg) from None
        if content_length > max_payload:
            logger.warning(
                WEBHOOK_REJECTED,
                connection_name=connection_name,
                reason="content-length exceeds max_payload_bytes",
                content_length=content_length,
                max_payload=max_payload,
            )
            msg = (
                f"Webhook payload exceeds configured max_payload_bytes ({max_payload})"
            )
            raise ValidationError(msg)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_payload:
            logger.warning(
                WEBHOOK_REJECTED,
                connection_name=connection_name,
                reason="body exceeds max_payload_bytes",
                body_length=total,
                max_payload=max_payload,
            )
            msg = (
                f"Webhook payload exceeds configured max_payload_bytes ({max_payload})"
            )
            raise ValidationError(msg)
        chunks.append(chunk)
    return b"".join(chunks)


async def _verify_signature(
    *,
    catalog: ConnectionCatalog,
    connection_name: str,
    connection_type: ConnectionType,
    body: bytes,
    headers: dict[str, str],
) -> None:
    """Verify the webhook signature, raising 401 on missing secret or mismatch.

    Raises:
        UnauthorizedError: Raised on the corresponding failure path.
    """
    verifier = get_verifier(connection_type)
    credentials = await catalog.get_credentials(connection_name)
    signing_secret = credentials.get(
        "signing_secret",
        credentials.get("webhook_secret", ""),
    )
    if not signing_secret:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="signing secret not configured",
        )
        msg = (
            "Webhook signing secret is not configured for this "
            "connection; request rejected"
        )
        raise UnauthorizedError(msg)
    valid = await verifier.verify(
        body=body,
        headers=headers,
        secret=signing_secret,
    )
    if not valid:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="signature verification failed",
        )
        msg = "Signature verification failed"
        raise UnauthorizedError(msg)


def _parse_timestamp(
    headers: dict[str, str],
    *,
    connection_name: str,
) -> float | None:
    """Defensive ``x-timestamp`` parse; ``None`` when absent.

    Returns:
        The ``float`` value when present, ``None`` otherwise.

    Raises:
        ValidationError: Raised on the corresponding failure path.
    """
    timestamp_str = headers.get("x-timestamp", "")
    if not timestamp_str:
        return None
    try:
        return float(timestamp_str)
    except ValueError:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="malformed x-timestamp header",
        )
        msg = "Malformed x-timestamp header"
        raise ValidationError(msg) from None


async def _check_replay_or_freshness(
    *,
    state: State,
    connection_name: str,
    nonce: str | None,
    timestamp: float | None,
) -> None:
    """In-memory replay/freshness guard; durable dedup runs separately.

    For nonce-bearing requests we only validate timestamp staleness
    here; durable IdempotencyService below handles dedup so a
    legitimate retry with the same nonce on a different replica
    receives the cached 202 instead of an early 409.

    The hard ``MAX_NONCE_CHARS`` cap that ``ReplayProtector.check``
    enforces in the no-nonce branch must apply here too -- otherwise
    an attacker can sidestep the limit by passing a freshness-only
    nonce and let the durable path try to hash an unbounded string.

    Raises:
        ConflictError: Raised on the corresponding failure path.
    """
    if nonce is not None and len(nonce) > MAX_NONCE_CHARS:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="nonce exceeds max size",
            nonce_length=len(nonce),
            max_nonce_chars=MAX_NONCE_CHARS,
        )
        msg = "Nonce exceeds maximum size"
        raise ConflictError(msg)
    replay_protector = await _get_replay_protector(state)
    if nonce:
        if replay_protector.check_freshness(timestamp):
            return
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="stale timestamp",
        )
        msg = "Replay detected (stale timestamp)"
        raise ConflictError(msg)
    if not replay_protector.check(nonce=nonce, timestamp=timestamp):
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="replay detected",
        )
        msg = "Replay detected (duplicate nonce or stale timestamp)"
        raise ConflictError(msg)


async def _publish_webhook_event_and_log(
    *,
    bus: MessageBus,
    connection_name: str,
    event_type: str,
    payload: Mapping[str, object],
    dedup_source: str,
) -> dict[str, object]:
    """Publish the event to the bus and emit ``WEBHOOK_ACCEPTED``.

    ``dedup_source`` carries the provenance of the idempotency key
    (``"nonce"`` for the standard ``X-Nonce`` / ``X-Request-Id`` path,
    ``"body_sha256"`` for the nonce-less path that hashes the request
    body). Surfacing the source on the success log lets operators
    distinguish well-behaved providers from those without nonces and
    spot redelivery patterns.

    Returns:
        Mapping with the declared key/value types.
    """
    await publish_webhook_event(
        bus=bus,
        connection_name=connection_name,
        event_type=event_type,
        payload=dict(payload),
    )
    logger.info(
        WEBHOOK_ACCEPTED,
        connection_name=connection_name,
        event_type=event_type,
        dedup_source=dedup_source,
    )
    return {"status": "accepted", "event_type": event_type}


async def _publish_with_durable_idempotency(  # noqa: PLR0913
    *,
    state: State,
    connection_name: str,
    event_type: str,
    nonce: str,
    connection_type: str,
    bus: MessageBus,
    payload: Mapping[str, object],
    dedup_source: str,
) -> dict[str, object]:
    """Run the publish under the durable :class:`IdempotencyService`.

    Returns the cached/fresh response body. Raises 409 on contention
    that the polling window could not resolve.

    Returns:
        Mapping with the declared key/value types.

    Raises:
        ConflictError: Raised on the corresponding failure path.
        TypeError: Raised on the corresponding failure path.
    """
    from synthorg.core.types import NotBlankStr  # noqa: PLC0415

    scope = NotBlankStr(
        _build_idem_scope(
            connection_type=connection_type,
            connection_name=connection_name,
        ),
    )
    idem_key = NotBlankStr(
        _build_idem_key(
            connection_name=connection_name,
            event_type=event_type,
            nonce=nonce,
        ),
    )

    async def _publish_and_accept() -> dict[str, object]:
        """Return publish and accept."""
        return await _publish_webhook_event_and_log(
            bus=bus,
            connection_name=connection_name,
            event_type=event_type,
            payload=payload,
            dedup_source=dedup_source,
        )

    outcome = await idempotency_service_of(state["app_state"]).run_idempotent(
        scope=scope,
        key=idem_key,
        callback=_publish_and_accept,
    )
    if outcome.timed_out:
        # Distinct from a callback that legitimately returned ``None``
        # -- we only 409 on actual in-flight timeouts /
        # leader-failure exhaustion.
        logger.warning(
            IDEMPOTENCY_CLAIM_IN_FLIGHT,
            scope=scope,
            idempotency_key=idem_key,
            connection_name=connection_name,
            event_type=event_type,
            endpoint="webhook.receive",
        )
        msg = "Concurrent in-flight webhook delivery"
        raise ConflictError(msg)
    cached = outcome.result
    # ``run_idempotent`` returns ``Any`` (the JSON-decoded cached
    # body); the only callbacks under this scope are
    # ``_publish_and_accept`` returning ``dict[str, object]``.
    # Narrow defensively rather than via ``assert`` so a regression
    # surfaces as a structured exception, not an AssertionError.
    if not isinstance(cached, dict):
        # Log the corruption with full webhook context so the operator
        # has enough forensics to identify which row in the durable
        # idempotency table is malformed; raising bare TypeError would
        # surface as a generic 500 with no breadcrumb back to the
        # offending claim.
        logger.error(
            IDEMPOTENCY_CLAIM_IN_FLIGHT,
            scope=scope,
            idempotency_key=idem_key,
            connection_name=connection_name,
            event_type=event_type,
            endpoint="webhook.receive",
            note="cached_response_type_mismatch",
            cached_type=type(cached).__name__,
        )
        msg = "Cached webhook response was not a JSON object"
        raise TypeError(msg)
    return cached
