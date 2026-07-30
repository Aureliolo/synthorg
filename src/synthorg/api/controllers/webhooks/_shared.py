"""Receive-path helpers for the webhooks ingest controller.

Pure helper module: connection lookup, streaming payload-size
enforcement, signature verification, timestamp parsing,
replay/freshness guarding, the bus-publish-and-log primitive, and the
durable-idempotency wrapper. The ingest controller imports these as
bare names (so tests patch them on the ingest module); the retry path
reaches ``_publish_webhook_event_and_log`` module-qualified through
this module so there is one canonical patch target.
"""

from collections.abc import Mapping
from typing import Final

from litestar import Request
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.api_core_state import idempotency_service_of
from synthorg.api.controllers._webhooks_wiring import (
    _build_idem_key,
    _build_idem_scope,
)
from synthorg.communication.bus_protocol import MessageBus
from synthorg.core.domain_errors import (
    ConflictError,
    UnauthorizedError,
    ValidationError,
)
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.field_metadata import (
    WEBHOOK_SIGNING_SECRET_FIELD,
    get_connection_type_metadata,
)
from synthorg.integrations.connections.models import Connection, ConnectionType
from synthorg.integrations.errors import (
    WebhookProcessingError,
    WebhookVerifierUnavailableError,
)
from synthorg.integrations.state import (
    IntegrationsStateSlice,
    webhook_replay_protector_of,
)
from synthorg.integrations.webhooks.event_bus_bridge import publish_webhook_event
from synthorg.integrations.webhooks.replay_protection import MAX_NONCE_CHARS
from synthorg.integrations.webhooks.verifiers.factory import get_verifier
from synthorg.observability import get_logger
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT
from synthorg.observability.events.integrations import (
    WEBHOOK_ACCEPTED,
    WEBHOOK_REJECTED,
)

logger = get_logger(__name__)

#: The single message every unauthenticated rejection carries.
#:
#: Ingest is reachable without credentials, so any distinction between "no such
#: connection", "this type has no verifier", "the secret is unset" and "the
#: signature did not match" is an oracle: an unauthenticated caller could
#: enumerate connection names and learn, per name, whether a signing secret is
#: configured. The distinction is kept in the structured log, where an operator
#: can see it and an attacker cannot.
_UNVERIFIABLE_DELIVERY: Final[str] = (
    "Webhook delivery could not be authenticated; request rejected"
)


async def _get_verified_connection(state: State, connection_name: str) -> Connection:
    """Look up the named connection, rejecting an unknown name as unauthorised.

    Returns:
        ``Connection`` instance.

    Raises:
        UnauthorizedError: When no such connection exists. Deliberately not a
            404: see :data:`_UNVERIFIABLE_DELIVERY`.
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
        raise UnauthorizedError(_UNVERIFIABLE_DELIVERY)
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
    connection: Connection,
    body: bytes,
    headers: dict[str, str],
) -> None:
    """Verify the webhook signature, raising 401 on missing secret or mismatch.

    Reads exactly one credential key, ``signing_secret``, the one the connection
    registry declares. Honouring a second undeclared name would open an ingest
    path no metadata-driven surface can see: the dashboard form and
    ``webhook_secret_field`` name only the declared field, and
    ``reject_inline_secret_fields`` can only refuse keys the registry knows are
    secret, so an undeclared alias could be posted inline through the create
    body and never appear as a credential anywhere an operator looks.

    Whitespace is stripped before the emptiness test: a blank-but-present secret
    is not a secret, and passing it through would hand the verifier a key an
    attacker can guess in one attempt.

    The secret field's own ``visible_when`` is resolved against the connection's
    stored values, so a secret captured while the field applied stops
    authenticating once it no longer does. Otherwise an operator who repointed a
    ``generic_http`` connection at a vendor preset would have retired the inbound
    path in every surface they can see while it kept publishing verified events.

    Raises:
        UnauthorizedError: Raised on the corresponding failure path.
    """
    connection_name = connection.name
    connection_type = connection.connection_type
    metadata = get_connection_type_metadata(connection_type)
    if not metadata.webhook_ingest_is_reachable(connection.metadata):
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            connection_type=connection_type.value,
            reason="signing secret does not apply to this connection",
        )
        raise UnauthorizedError(_UNVERIFIABLE_DELIVERY)
    try:
        verifier = get_verifier(connection_type)
    except WebhookVerifierUnavailableError:
        # Collapsed into the same 401 rather than surfacing 501: a distinct
        # status tells an unauthenticated caller that the connection exists and
        # which types are ingest-capable.
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            connection_type=connection_type.value,
            reason="no verifier registered for connection type",
        )
        raise UnauthorizedError(_UNVERIFIABLE_DELIVERY) from None
    credentials = await catalog.get_credentials(connection_name)
    signing_secret = credentials.get(WEBHOOK_SIGNING_SECRET_FIELD, "").strip()
    if not signing_secret:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="signing secret not configured",
        )
        raise UnauthorizedError(_UNVERIFIABLE_DELIVERY)
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
        raise UnauthorizedError(_UNVERIFIABLE_DELIVERY)


def _read_delivery_id(
    headers: dict[str, str],
    connection_type: ConnectionType,
) -> str | None:
    """Read the sender's own delivery id, for logging only.

    Each provider names it differently, so the verifier declares the header;
    ``None`` for a scheme that sends none. Not used for deduplication: the id is
    outside the signature and therefore attacker-controlled, which is exactly
    why :func:`_check_replay_or_freshness` keys on the body instead.

    Returns:
        The trimmed delivery id, or ``None`` when absent or unsupported.
    """
    try:
        header = get_verifier(connection_type).delivery_id_header
    except WebhookVerifierUnavailableError:  # pragma: no cover -- verified first
        return None
    if header is None:
        return None
    return (headers.get(header) or "").strip() or None


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
    dedup_key: str,
    delivery_id: str | None,
    timestamp: float | None,
) -> None:
    """In-memory replay/freshness guard; durable dedup runs separately.

    Deduplicates on ``dedup_key``, which the caller derives from the request
    body, and never on a header value alone. Header-supplied ids are not covered
    by any verifier's signature (the HMAC schemes sign the body only, and
    GitLab's token scheme signs nothing), so an attacker holding one captured
    signed body could replay it unlimited times simply by varying the id: each
    fresh value looked unseen, minted a fresh idempotency key, and published
    another verified event. Keying on the body means a captured delivery
    collapses onto its first publish however its headers are dressed up.

    ``delivery_id`` is logged for traceability but does not weaken the check.

    The hard ``MAX_NONCE_CHARS`` cap ``ReplayProtector.check`` enforces applies
    to the derived key too, so an unbounded value can never reach the durable
    path's hashing.

    Raises:
        ConflictError: Raised on the corresponding failure path.
    """
    if len(dedup_key) > MAX_NONCE_CHARS:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="dedup key exceeds max size",
            key_length=len(dedup_key),
            max_nonce_chars=MAX_NONCE_CHARS,
        )
        msg = "Dedup key exceeds maximum size"
        raise ConflictError(msg)
    replay_protector = webhook_replay_protector_of(state["app_state"])
    if not replay_protector.check(nonce=dedup_key, timestamp=timestamp):
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            delivery_id=delivery_id,
            reason="replay detected",
        )
        msg = "Replay detected (duplicate delivery or stale timestamp)"
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

    ``dedup_source`` carries the provenance of the idempotency key:
    ``"body_sha256"`` for inbound ingest, which always keys on the body digest,
    and ``"manual_retry"`` for an operator-triggered redelivery. Surfacing it on
    the success log is what distinguishes a sender's own retry from a human
    replaying one.

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


async def _publish_with_durable_idempotency(
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
        WebhookProcessingError: If the cached idempotent response is not
            a JSON object (corrupt cache entry).
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
        raise WebhookProcessingError(msg)
    return cached
