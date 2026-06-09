"""Retry-path helpers for the webhooks retry controller.

Pure helper module: receipt-status constants, payload decode, the
compare-and-set receipt-status transition writer, the retryable-state
guard, and the publish-and-transition body of the retry endpoint. The
bus publish reaches ``_publish_webhook_event_and_log`` module-qualified
through :mod:`_shared` so it shares one canonical patch target with the
ingest path.
"""

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.api.controllers.webhooks import _shared
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.integrations import (
    WEBHOOK_RECEIPT_NOT_FOUND,
    WEBHOOK_RECEIPT_STATUS_TRANSITIONED,
    WEBHOOK_REJECTED,
)

if TYPE_CHECKING:
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

# Receipt-status strings shared between the persistence-write and the
# status-transition log so the wire value stays in one place.
_RECEIPT_STATUS_RETRYING: Final[str] = "retrying"
_RECEIPT_STATUS_RECEIVED: Final[str] = "received"
_RECEIPT_STATUS_FAILED: Final[str] = "failed"


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

    Returns:
        Mapping with the declared key/value types.
    """
    try:
        raw_payload = json.loads(receipt.payload_json)
    except json.JSONDecodeError, UnicodeDecodeError:
        raw_payload = {"raw": receipt.payload_json}
    if isinstance(raw_payload, dict):
        return dict(raw_payload)
    return {"data": raw_payload}


async def _transition_webhook_receipt_status(  # noqa: PLR0913
    persistence: PersistenceBackend,
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

    Raises:
        NotFoundError: Raised on the corresponding failure path.
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

    Raises:
        ConflictError: Raised on the corresponding failure path.
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
    persistence: PersistenceBackend,
    bus: MessageBus,
    receipt: WebhookReceipt,
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Do the publish-and-CAS body of ``retry_receipt`` under idempotency.

    Returns the publish-response dict augmented with ``receipt_id``,
    matching the contract of the un-wrapped previous implementation so
    cached idempotency results stay compatible with prior dashboards.

    Returns:
        Mapping with the declared key/value types.

    Raises:
        asyncio.CancelledError: Re-raised after marking the receipt
            failed, so a cancelled retry never sticks in ``retrying``.
        Exception: Raised on the corresponding failure path.
    """
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
        # Module-qualified (not ``from _shared import``) so the ingest and
        # retry paths share ONE canonical patch target for tests.
        result = await _shared._publish_webhook_event_and_log(  # noqa: SLF001
            bus=bus,
            connection_name=str(receipt.connection_name),
            event_type=receipt.event_type or "",
            payload=payload,
            dedup_source="manual_retry",
        )
    except asyncio.CancelledError as exc:
        # CancelledError is a BaseException, so the broad ``except
        # Exception`` below never sees it. Without this branch a
        # cancelled retry leaves the receipt stuck in ``retrying``;
        # mark it failed, then propagate the cancellation.
        await _transition_webhook_receipt_status(
            persistence,
            receipt,
            new_status=_RECEIPT_STATUS_FAILED,
            previous=_RECEIPT_STATUS_RETRYING,
            processed_at=datetime.now(UTC),
            error=safe_error_description(exc),
        )
        raise
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            WEBHOOK_REJECTED,
            exc,
            receipt_id=str(receipt.id),
            connection_name=str(receipt.connection_name),
            reason="retry_publish_failed",
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
