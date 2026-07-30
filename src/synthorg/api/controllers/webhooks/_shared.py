"""Receive-path helpers for the webhooks ingest controller.

What ingest does once a delivery has authenticated: streaming payload-size
enforcement, timestamp parsing, replay/freshness guarding, the
bus-publish-and-log primitive, and the durable-idempotency wrapper.
Authentication itself lives in ``_authentication``. The ingest controller
imports these as bare names (so tests patch them on the ingest module); the
retry path reaches ``_publish_webhook_event_and_log`` module-qualified through
this module so there is one canonical patch target.
"""

from collections.abc import Mapping

from litestar import Request
from litestar.datastructures import State

from synthorg.api.api_core_state import idempotency_service_of
from synthorg.api.controllers._webhooks_wiring import (
    _build_idem_key,
    _build_idem_scope,
)
from synthorg.communication.bus_protocol import MessageBus
from synthorg.core.domain_errors import ConflictError, ValidationError
from synthorg.integrations.errors import WebhookProcessingError
from synthorg.integrations.state import webhook_replay_protector_of
from synthorg.integrations.webhooks.event_bus_bridge import publish_webhook_event
from synthorg.integrations.webhooks.replay_protection import MAX_NONCE_CHARS
from synthorg.observability import get_logger
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT
from synthorg.observability.events.integrations import (
    WEBHOOK_ACCEPTED,
    WEBHOOK_REJECTED,
)

logger = get_logger(__name__)


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

    Deduplicates on ``dedup_key``, the delivery identity
    :func:`build_delivery_key` composed, and never on a header value alone.
    Header-supplied ids are not covered by any verifier's signature (the HMAC
    schemes sign the body only, and GitLab's token scheme signs nothing), so an
    attacker holding one captured signed body could replay it unlimited times
    simply by varying the id: each fresh value looked unseen, minted a fresh
    idempotency key, and published another verified event.

    Both gates take the identical key, which is the point: this one bounds a
    replay within its window, and the durable one bounds it for the whole TTL
    across replicas. Keyed differently, each dimension is only as guarded as
    whichever gate happens to cover it.

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
    delivery_key: str,
    connection_type: str,
    bus: MessageBus,
    payload: Mapping[str, object],
    dedup_source: str,
) -> dict[str, object]:
    """Run the publish under the durable :class:`IdempotencyService`.

    Returns the cached/fresh response body. Raises 409 on contention
    that the polling window could not resolve.

    ``event_type`` is published but is deliberately NOT part of the key. It
    comes from the URL and no verifier signs the path, so keying on it would let
    one captured signed body mint a fresh verified publish per event name the
    attacker chose to post it to. The key is the delivery identity
    :func:`build_delivery_key` composed, which the in-memory replay gate already
    asserted on the same request.

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
    idem_key = NotBlankStr(_build_idem_key(delivery_key=delivery_key))

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
