# module-kind: service
"""WebhookReceiptService: owns webhook-receipt lifecycle transitions.

This service is the single owner of webhook-receipt persistence access: the
receipt lookup, the ``failed -> retrying -> received`` (or ``-> failed``)
compare-and-set transitions, and the publish-and-transition orchestration. The
``api`` layer reaches it through the service rather than touching
``persistence.webhook_receipts`` directly, keeping the controller free of
repository internals. The bus publish itself stays in the API layer and is
injected as a callback, so this integrations-layer service never reaches up
into ``api`` to reach the message bus.

Timestamps come from an injected :class:`Clock` so the transition instants are
deterministic under test.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.types import NotBlankStr
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
    # WebhookReceiptRepository is a runtime_checkable protocol injected via a
    # duck-typed stub in API-layer tests; a runtime import would make typeguard
    # reject the incomplete stub.
    from synthorg.persistence.connection_protocol import WebhookReceiptRepository

logger = get_logger(__name__)

# Receipt-status strings shared between the persistence-write and the
# status-transition log so the wire value stays in one place.
_RECEIPT_STATUS_RETRYING: Final[str] = "retrying"
_RECEIPT_STATUS_RECEIVED: Final[str] = "received"
_RECEIPT_STATUS_FAILED: Final[str] = "failed"

#: Zero-arg publish bridge supplied by the API layer; returns the publish
#: response envelope. Injected so the integrations service never imports the
#: API-layer bus-publish helper.
PublishCallback = Callable[[], Awaitable[dict[str, object]]]


class WebhookReceiptService:
    """Service facade owning webhook-receipt reads and lifecycle writes.

    Args:
        receipts_repo: Backing :class:`WebhookReceiptRepository`. The service
            does not own the repo's lifecycle; the application wiring supplies
            it from the connected persistence backend at startup.
        clock: Clock seam for transition timestamps; defaults to
            :class:`SystemClock`.
    """

    def __init__(
        self,
        *,
        receipts_repo: WebhookReceiptRepository,
        clock: Clock | None = None,
    ) -> None:
        self._receipts_repo = receipts_repo
        self._clock = clock or SystemClock()

    async def get(self, receipt_id: NotBlankStr) -> WebhookReceipt | None:
        """Fetch a single receipt by id, or ``None`` when absent.

        Returns:
            The :class:`WebhookReceipt`, or ``None`` when no row matches.
        """
        return await self._receipts_repo.get(receipt_id)

    @staticmethod
    def assert_retryable(receipt: WebhookReceipt) -> None:
        """Raise ``ConflictError`` when the receipt is not in ``failed``.

        The retry endpoint exists to re-publish deliveries the downstream
        consumer failed on. A stale dashboard link must not let an operator
        replay a ``received`` row (would double-publish a successful delivery)
        or a ``retrying`` row (would race an in-flight attempt). Reject up
        front before any persistence write or bus publish.

        Raises:
            ConflictError: When the receipt's status is not ``failed``.
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

    @staticmethod
    def load_payload(receipt: WebhookReceipt) -> dict[str, object]:
        """Decode the stored ``payload_json`` into a publish-ready dict.

        Falls back to ``{"raw": <payload_json>}`` when the stored text is not
        valid JSON, and to ``{"data": <value>}`` when the JSON parses to a
        non-mapping (lists / scalars). An empty ``payload_json`` (``""`` -- a
        webhook captured with a zero-byte body) flows through the
        ``json.loads`` failure path to ``{"raw": ""}`` so retries preserve the
        same envelope shape ``receive_webhook`` produced on the original
        delivery.

        Returns:
            Mapping with the declared key/value types.
        """
        try:
            raw_payload = json.loads(receipt.payload_json)
        except json.JSONDecodeError:
            raw_payload = {"raw": receipt.payload_json}
        if isinstance(raw_payload, dict):
            return dict(raw_payload)
        return {"data": raw_payload}

    async def retry_and_publish(
        self,
        receipt: WebhookReceipt,
        *,
        publish: PublishCallback,
    ) -> dict[str, object]:
        """Run the publish-and-CAS body of a receipt retry.

        Transitions ``failed -> retrying`` under compare-and-set, invokes the
        injected *publish* bridge, then transitions ``retrying -> received``
        on success or ``retrying -> failed`` on publish failure /
        cancellation. Returns the publish-response dict augmented with
        ``receipt_id``.

        Returns:
            The publish response envelope plus ``receipt_id``.

        Raises:
            NotFoundError: When a transition's row is missing or its CAS is
                lost (a concurrent retry already claimed the row).
            asyncio.CancelledError: Re-raised after marking the receipt
                failed, so a cancelled retry never sticks in ``retrying``.
            Exception: Any publish failure, re-raised after marking failed.
        """
        await self._transition(
            receipt,
            new_status=_RECEIPT_STATUS_RETRYING,
            previous=receipt.status,
            processed_at=None,
            error=None,
            cas_from=_RECEIPT_STATUS_FAILED,
        )
        try:
            result = await publish()
        except asyncio.CancelledError as exc:
            # CancelledError is a BaseException, so the broad ``except
            # Exception`` below never sees it. Without this branch a cancelled
            # retry leaves the receipt stuck in ``retrying``; mark it failed,
            # then propagate the cancellation. The cleanup write is shielded so
            # the still-pending cancellation cannot re-interrupt it mid-write
            # and re-strand the receipt in ``retrying``.
            await asyncio.shield(
                self._transition(
                    receipt,
                    new_status=_RECEIPT_STATUS_FAILED,
                    previous=_RECEIPT_STATUS_RETRYING,
                    processed_at=self._clock.now(),
                    error=safe_error_description(exc),
                )
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
            # Shielded so a cancellation delivered during this cleanup write
            # cannot interrupt it and leave the receipt stuck in ``retrying``.
            await asyncio.shield(
                self._transition(
                    receipt,
                    new_status=_RECEIPT_STATUS_FAILED,
                    previous=_RECEIPT_STATUS_RETRYING,
                    processed_at=self._clock.now(),
                    error=safe_error_description(exc),
                )
            )
            raise
        # The publish already succeeded; shield the success transition so a
        # cancellation here cannot leave a delivered webhook marked ``retrying``.
        await asyncio.shield(
            self._transition(
                receipt,
                new_status=_RECEIPT_STATUS_RECEIVED,
                previous=_RECEIPT_STATUS_RETRYING,
                processed_at=self._clock.now(),
                error=None,
            )
        )
        return {**result, "receipt_id": str(receipt.id)}

    async def _transition(  # noqa: PLR0913 -- transition fields, all internal
        self,
        receipt: WebhookReceipt,
        *,
        new_status: str,
        previous: str | None,
        processed_at: datetime | None,
        error: str | None,
        cas_from: str | None = None,
    ) -> None:
        """Persist a receipt-status transition, logging on success / miss.

        ``cas_from`` enables compare-and-set: when supplied, the UPDATE only
        fires when the row's current ``status`` equals ``cas_from`` (two
        concurrent retries cannot both pass). The
        ``WEBHOOK_RECEIPT_STATUS_TRANSITIONED`` INFO event lands only on a
        successful write so the log never claims a transition the DB never
        accepted; a missing row (or a lost CAS race) raises ``NotFoundError``.

        Raises:
            NotFoundError: When the row is missing or the CAS race was lost.
        """
        if cas_from is not None:
            updated = await self._receipts_repo.update_status_if_current(
                NotBlankStr(receipt.id),
                expected_status=cas_from,
                status=new_status,
                processed_at=processed_at,
                error=error,
            )
        else:
            updated = await self._receipts_repo.update_status(
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


__all__ = ["PublishCallback", "WebhookReceiptService"]
