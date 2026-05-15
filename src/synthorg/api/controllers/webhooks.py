"""Webhooks API controller.

Receives webhook events from external services, verifies
signatures, and publishes to the message bus.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any, Final

from litestar import Controller, Request, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.boundary import parse_typed
from synthorg.api.controllers._webhooks_wiring import (
    _IDEMPOTENCY_KEY_MAX_LEN,
    WebhookEventPayload,
    _build_idem_key,
    _build_idem_scope,
    _get_activity_service,
    _get_replay_protector,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import (  # noqa: TC001 -- runtime annotation
    PathEventType,
    PathName,
)
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from synthorg.integrations.connections.models import WebhookReceipt  # noqa: TC001
from synthorg.integrations.webhooks.event_bus_bridge import (
    publish_webhook_event,
)
from synthorg.integrations.webhooks.replay_protection import MAX_NONCE_CHARS
from synthorg.integrations.webhooks.verifiers.factory import get_verifier
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_VALIDATION_FAILED
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT
from synthorg.observability.events.integrations import (
    WEBHOOK_ACCEPTED,
    WEBHOOK_RECEIPT_NOT_FOUND,
    WEBHOOK_RECEIPT_STATUS_TRANSITIONED,
    WEBHOOK_RECEIVED,
    WEBHOOK_REJECTED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from synthorg.integrations.connections.models import ConnectionType

logger = get_logger(__name__)

# Receipt-status strings shared between the persistence-write and the
# status-transition log so the wire value stays in one place.
_RECEIPT_STATUS_RETRYING: Final[str] = "retrying"
_RECEIPT_STATUS_RECEIVED: Final[str] = "received"
_RECEIPT_STATUS_FAILED: Final[str] = "failed"

__all__ = [
    "_IDEMPOTENCY_KEY_MAX_LEN",
    "WebhookEventPayload",
    "WebhooksController",
    "_build_idem_key",
    "_build_idem_scope",
    "_get_activity_service",
    "_get_replay_protector",
]


async def _get_connection_or_404(state: State, connection_name: str) -> Any:
    """Look up the named connection or raise 404 with a logged reason."""
    catalog = state["app_state"].connection_catalog
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
    request: Request[Any, Any, Any],
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
    catalog: Any,
    connection_name: str,
    connection_type: ConnectionType,
    body: bytes,
    headers: dict[str, str],
) -> None:
    """Verify the webhook signature, raising 401 on missing secret or mismatch."""
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
    """Defensive ``x-timestamp`` parse; ``None`` when absent."""
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
    bus: Any,
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
    bus: Any,
    payload: Mapping[str, object],
    dedup_source: str,
) -> dict[str, object]:
    """Run the publish under the durable :class:`IdempotencyService`.

    Returns the cached/fresh response body. Raises 409 on contention
    that the polling window could not resolve.
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
        return await _publish_webhook_event_and_log(
            bus=bus,
            connection_name=connection_name,
            event_type=event_type,
            payload=payload,
            dedup_source=dedup_source,
        )

    outcome = await state["app_state"].idempotency_service.run_idempotent(
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


def _load_payload_from_receipt(receipt: WebhookReceipt) -> dict[str, object]:
    """Decode the stored ``payload_json`` into a publish-ready dict.

    Falls back to ``{"raw": <bytes>}`` when the stored payload is not
    valid JSON (the original webhook may have been a binary or
    non-UTF8 body the verifier accepted but the JSON decoder cannot
    re-parse) and to ``{"data": <value>}`` when the JSON parses to a
    non-mapping (lists / scalars). The publish bridge always receives
    a ``dict[str, object]`` so downstream consumers can rely on the
    envelope shape.

    An empty ``payload_json`` (``""`` -- a webhook captured with a
    zero-byte body) flows through the ``json.loads`` failure path to
    ``{"raw": ""}`` rather than being short-circuited to ``{}``, so
    retries preserve the same envelope shape ``receive_webhook``
    produced on the original delivery.
    """
    try:
        raw_payload = json.loads(receipt.payload_json)
    except json.JSONDecodeError, UnicodeDecodeError:
        raw_payload = {"raw": receipt.payload_json}
    if isinstance(raw_payload, dict):
        return dict(raw_payload)
    return {"data": raw_payload}


async def _transition_webhook_receipt_status(  # noqa: PLR0913
    persistence: Any,
    receipt: WebhookReceipt,
    *,
    new_status: str,
    previous: str | None,
    processed_at: datetime | None,
    error: str | None,
    cas_from: str | None = None,
) -> None:
    """Persist a receipt-status transition, logging on success / miss.

    ``cas_from`` enables compare-and-set: when supplied, the UPDATE
    only fires when the row's current ``status`` column equals
    ``cas_from`` (two concurrent retries cannot both pass). The
    ``WEBHOOK_RECEIPT_STATUS_TRANSITIONED`` INFO event lands only on
    a successful write so the log never claims a transition the DB
    never accepted; a missing row (or a lost CAS race) raises
    ``NotFoundError`` so the caller can surface 404 instead of
    pretending the transition happened.
    """
    from synthorg.core.types import NotBlankStr  # noqa: PLC0415

    if cas_from is not None:
        updated = await persistence.webhook_receipts.update_status_if_current(
            NotBlankStr(receipt.id),
            expected_status=cas_from,
            status=new_status,
            processed_at=processed_at,
            error=error,
        )
    else:
        updated = await persistence.webhook_receipts.update_status(
            NotBlankStr(receipt.id),
            status=new_status,
            processed_at=processed_at,
            error=error,
        )
    if not updated:
        logger.warning(
            WEBHOOK_RECEIPT_NOT_FOUND,
            receipt_id=str(receipt.id),
            connection_name=str(receipt.connection_name),
            reason=(
                "cas_lost_or_row_missing"
                if cas_from is not None
                else "status_transition_row_missing"
            ),
            target_status=new_status,
            previous_status=previous,
            expected_status=cas_from,
        )
        msg = f"Webhook receipt {receipt.id!r} not found"
        raise NotFoundError(msg)
    logger.info(
        WEBHOOK_RECEIPT_STATUS_TRANSITIONED,
        receipt_id=str(receipt.id),
        connection_name=str(receipt.connection_name),
        previous_status=previous,
        status=new_status,
    )


def _assert_receipt_retryable(receipt: WebhookReceipt) -> None:
    """Raise ``ConflictError`` when the receipt is not in ``failed``.

    The retry endpoint exists to re-publish deliveries the downstream
    consumer failed on. A stale dashboard link must not let an
    operator replay a ``received`` row (would double-publish a
    successful delivery) or a ``retrying`` row (would race against
    an in-flight attempt). Reject up front before any persistence
    write or bus publish.
    """
    if receipt.status == _RECEIPT_STATUS_FAILED:
        return
    logger.warning(
        WEBHOOK_REJECTED,
        receipt_id=str(receipt.id),
        connection_name=str(receipt.connection_name),
        reason="retry_requires_failed_status",
        current_status=receipt.status,
    )
    msg = (
        f"Webhook receipt {receipt.id!r} is not retryable "
        f"(current status: {receipt.status!r}); only "
        f"{_RECEIPT_STATUS_FAILED!r} receipts can be retried"
    )
    raise ConflictError(msg)


async def _retry_publish_and_transition(
    *,
    persistence: Any,
    bus: Any,
    receipt: WebhookReceipt,
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Do the publish-and-CAS body of ``retry_receipt`` under idempotency.

    Returns the publish-response dict augmented with ``receipt_id``,
    matching the contract of the un-wrapped previous implementation so
    cached idempotency results stay compatible with prior dashboards.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    await _transition_webhook_receipt_status(
        persistence,
        receipt,
        new_status=_RECEIPT_STATUS_RETRYING,
        previous=receipt.status,
        processed_at=None,
        error=None,
        cas_from=_RECEIPT_STATUS_FAILED,
    )
    try:
        result = await _publish_webhook_event_and_log(
            bus=bus,
            connection_name=str(receipt.connection_name),
            event_type=receipt.event_type or "",
            payload=payload,
            dedup_source="manual_retry",
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.error(
            WEBHOOK_REJECTED,
            receipt_id=str(receipt.id),
            connection_name=str(receipt.connection_name),
            reason="retry_publish_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        await _transition_webhook_receipt_status(
            persistence,
            receipt,
            new_status=_RECEIPT_STATUS_FAILED,
            previous=_RECEIPT_STATUS_RETRYING,
            processed_at=datetime.now(UTC),
            error=safe_error_description(exc),
        )
        raise
    await _transition_webhook_receipt_status(
        persistence,
        receipt,
        new_status=_RECEIPT_STATUS_RECEIVED,
        previous=_RECEIPT_STATUS_RETRYING,
        processed_at=datetime.now(UTC),
        error=None,
    )
    return {**result, "receipt_id": str(receipt.id)}


class WebhooksController(Controller):
    """Webhook receiver and activity log endpoints."""

    path = "/webhooks"
    tags = ["Integrations"]  # noqa: RUF012

    @post(
        "/{connection_name:str}/{event_type:str}",
        summary="Receive a webhook event",
        status_code=202,
        guards=[
            # External-facing endpoint (no auth): key on IP.
            # 120/60s = sustained 2 rps per client; signature verification
            # and replay protection run BELOW this guard.
            per_op_rate_limit_from_policy("webhooks.receive", key="ip"),
        ],
    )
    async def receive_webhook(
        self,
        state: State,
        request: Request[Any, Any, Any],
        connection_name: PathName,
        event_type: PathEventType,
    ) -> ApiResponse[dict[str, object]]:
        """Receive and verify a webhook event.

        Thin orchestrator: delegates to module-level helpers for
        connection lookup, payload-size enforcement, signature
        verification, replay/freshness, durable idempotency, and
        bus publication. Returns 202 Accepted on success; raises
        structured errors (404 on unknown connection, 401 on missing
        or failed signature, 400 on malformed timestamp, 409 on
        replay).
        """
        catalog = state["app_state"].connection_catalog
        conn = await _get_connection_or_404(state, connection_name)
        logger.info(
            WEBHOOK_RECEIVED,
            connection_name=connection_name,
            event_type=event_type,
        )

        webhook_cfg = state["app_state"].config.integrations.webhooks
        body = await _enforce_max_payload(
            request,
            connection_name=connection_name,
            max_payload=webhook_cfg.max_payload_bytes,
        )
        headers = {k.lower(): v for k, v in request.headers.items()}

        await _verify_signature(
            catalog=catalog,
            connection_name=connection_name,
            connection_type=conn.connection_type,
            body=body,
            headers=headers,
        )

        # Strip each candidate header individually before fallback
        # selection. ``headers.get("x-nonce") or
        # headers.get("x-request-id")`` short-circuits on a
        # whitespace-only ``x-nonce`` (truthy before ``.strip()``)
        # and never tries ``x-request-id``, which routes real retries
        # down the body-hash path and changes the idempotency key.
        # Stripping each candidate first picks the first non-empty
        # value, or ``None``.
        nonce = next(
            (
                candidate
                for candidate in (
                    (headers.get("x-nonce") or "").strip(),
                    (headers.get("x-request-id") or "").strip(),
                )
                if candidate
            ),
            None,
        )
        timestamp = _parse_timestamp(headers, connection_name=connection_name)
        await _check_replay_or_freshness(
            state=state,
            connection_name=connection_name,
            nonce=nonce,
            timestamp=timestamp,
        )

        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                connection_name=connection_name,
                reason="webhook_body_not_json",
                error_type=type(exc).__name__,
            )
            msg = "webhook body is not valid JSON"
            raise ValidationError(msg) from None
        if not isinstance(decoded, dict):
            logger.warning(
                API_VALIDATION_FAILED,
                connection_name=connection_name,
                reason="webhook_body_not_object",
                wire_type=type(decoded).__name__,
            )
            msg = "webhook body must be a JSON object"
            raise ValidationError(msg)
        typed_payload = parse_typed("webhook.payload", decoded, WebhookEventPayload)
        normalized_payload: dict[str, object] = typed_payload.model_dump()

        bus = state["app_state"].message_bus

        # Both branches below publish through the durable
        # idempotency service so JetStream redelivery / retried POSTs
        # cannot double-bus the same event. When the provider
        # supplies a nonce / request-id we use that directly;
        # otherwise we synthesise one from the body's SHA-256 so
        # byte-identical redeliveries collapse to a single publish.
        # The ``dedup_source`` tag on the success log lets operators
        # distinguish the two paths in audit traces.
        if nonce:
            idem_nonce = nonce
            dedup_source = "nonce"
        else:
            body_digest = hashlib.sha256(body).hexdigest()
            idem_nonce = f"sha256:{body_digest}"
            dedup_source = "body_sha256"

        cached = await _publish_with_durable_idempotency(
            state=state,
            connection_name=connection_name,
            event_type=event_type,
            nonce=idem_nonce,
            connection_type=conn.connection_type,
            bus=bus,
            payload=normalized_payload,
            dedup_source=dedup_source,
        )
        return ApiResponse(data=cached)

    @get(
        "/{connection_name:str}/activity",
        guards=[require_read_access],
        summary="List webhook activity for a connection",
    )
    async def list_activity(
        self,
        state: State,
        connection_name: PathName,
        limit: int = Parameter(
            default=100,
            ge=1,
            le=500,
            description="Max results",
        ),
    ) -> ApiResponse[tuple[WebhookReceipt, ...]]:
        """List recent webhook receipts for a connection."""
        service = await _get_activity_service(state)
        receipts = await service.list_activity(
            connection_name=connection_name,
            limit=limit,
        )
        return ApiResponse(data=receipts)

    @post(
        "/receipts/{receipt_id:str}/retry",
        # Mutating endpoint: re-publishes onto the bus and transitions
        # the persisted receipt's status. ``require_write_access`` keeps
        # read-only principals out of the retry path (matches the
        # mutation-vs-listing split on adjacent handlers).
        guards=[require_write_access],
        summary="Retry a failed webhook receipt",
        status_code=202,
    )
    async def retry_receipt(
        self,
        state: State,
        receipt_id: str,
    ) -> ApiResponse[dict[str, object]]:
        """Re-publish a stored webhook payload to the message bus.

        Looks up the receipt, asserts it is in ``failed`` (only
        retryable state), wraps the publish-and-transition body in
        :meth:`IdempotencyService.run_idempotent` with scope
        ``"webhooks:retry"`` and the receipt id as the key. The CAS
        ``failed`` to ``retrying`` to ``received`` chain still runs
        inside the callback, so a duplicate operator click hits the
        idempotency cache instead of attempting a second transition
        that the CAS guard would reject anyway.

        Heavy lifting lives in module-level helpers
        (``_load_payload_from_receipt``, ``_assert_receipt_retryable``,
        ``_transition_webhook_receipt_status``) so this orchestrator
        stays under the repository's 50-line function cap.
        """
        from synthorg.core.types import NotBlankStr  # noqa: PLC0415

        persistence = state["app_state"].persistence
        receipt = await persistence.webhook_receipts.get(NotBlankStr(receipt_id))
        if receipt is None:
            logger.warning(
                WEBHOOK_RECEIPT_NOT_FOUND,
                receipt_id=receipt_id,
                reason="receipt_lookup_returned_none",
                stage="retry_lookup",
            )
            msg = f"Webhook receipt {receipt_id!r} not found"
            raise NotFoundError(msg)
        _assert_receipt_retryable(receipt)

        payload = _load_payload_from_receipt(receipt)
        bus = state["app_state"].message_bus
        # Snapshot the payload mapping so a re-invocation of the
        # idempotency callback cannot observe a mutated capture.
        payload_snapshot: Mapping[str, object] = dict(payload)

        async def _do_retry() -> dict[str, object]:
            return await _retry_publish_and_transition(
                persistence=persistence,
                bus=bus,
                receipt=receipt,
                payload=payload_snapshot,
            )

        scope = NotBlankStr("webhooks:retry")
        idem_key = NotBlankStr(receipt_id)
        outcome = await state["app_state"].idempotency_service.run_idempotent(
            scope=scope,
            key=idem_key,
            callback=_do_retry,
        )
        if outcome.timed_out:
            logger.warning(
                IDEMPOTENCY_CLAIM_IN_FLIGHT,
                scope=scope,
                idempotency_key=idem_key,
                receipt_id=str(receipt.id),
                connection_name=str(receipt.connection_name),
                endpoint="webhook.retry",
            )
            msg = "Concurrent in-flight webhook retry"
            raise ConflictError(msg)
        cached = outcome.result
        if not isinstance(cached, dict):
            logger.error(
                IDEMPOTENCY_CLAIM_IN_FLIGHT,
                scope=scope,
                idempotency_key=idem_key,
                receipt_id=str(receipt.id),
                connection_name=str(receipt.connection_name),
                endpoint="webhook.retry",
                note="cached_response_type_mismatch",
                cached_type=type(cached).__name__,
            )
            msg = "Cached retry response was not a JSON object"
            raise TypeError(msg)
        return ApiResponse(data=cached)
