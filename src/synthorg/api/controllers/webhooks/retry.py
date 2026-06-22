# module-kind: controller
"""Webhook receipt retry endpoint."""

from litestar import Controller, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.api_core_state import idempotency_service_of
from synthorg.api.controllers.webhooks import _shared
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_write_access
from synthorg.api.path_params import PathId
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import WebhookProcessingError
from synthorg.integrations.state import webhook_receipt_service_of
from synthorg.observability import get_logger
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT
from synthorg.observability.events.integrations import WEBHOOK_RECEIPT_NOT_FOUND

logger = get_logger(__name__)


class WebhooksRetryController(Controller):
    """Webhook receipt retry endpoint."""

    path = "/webhooks"
    tags = ["Integrations"]  # noqa: RUF012

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
        receipt_id: PathId,
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

        Heavy lifting lives in :class:`WebhookReceiptService` (receipt
        lookup, the retryable guard, payload decode, and the CAS transition
        chain) so this orchestrator only owns the idempotency wrapping and
        the bus-publish bridge it injects into the service.

        Returns:
            ``ApiResponse[dict[str, object]]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            ConflictError: Raised on the corresponding failure path.
            WebhookProcessingError: If the cached idempotent response is
                not a JSON object (corrupt cache entry).
        """
        receipt_service = webhook_receipt_service_of(state["app_state"])
        receipt = await receipt_service.get(NotBlankStr(receipt_id))
        if receipt is None:
            logger.warning(
                WEBHOOK_RECEIPT_NOT_FOUND,
                receipt_id=receipt_id,
                reason="receipt_lookup_returned_none",
                stage="retry_lookup",
            )
            msg = f"Webhook receipt {receipt_id!r} not found"
            raise NotFoundError(msg)
        bus = require_service(
            state["app_state"].slice(CommunicationStateSlice).message_bus,
            "Message Bus",
        )

        async def _do_retry() -> dict[str, object]:
            # Validate retryability INSIDE the idempotent claim. The
            # first request flips the row out of ``failed``; a
            # duplicate/manual retry must resolve through the same
            # claim's cached result rather than getting a 409 from a
            # pre-claim retryability check against the now-mutated row.
            """Return do retry."""
            receipt_service.assert_retryable(receipt)
            payload = receipt_service.load_payload(receipt)

            async def _publish() -> dict[str, object]:
                # The bus-publish bridge lives in the API layer; injecting it
                # keeps the integrations-layer service free of an upward
                # import. Module-qualified so the ingest and retry paths share
                # ONE canonical patch target for tests.
                return await _shared._publish_webhook_event_and_log(  # noqa: SLF001
                    bus=bus,
                    connection_name=str(receipt.connection_name),
                    event_type=receipt.event_type or "",
                    payload=dict(payload),
                    dedup_source="manual_retry",
                )

            return await receipt_service.retry_and_publish(
                receipt,
                publish=_publish,
            )

        scope = NotBlankStr("webhooks:retry")
        idem_key = NotBlankStr(receipt_id)
        outcome = await idempotency_service_of(state["app_state"]).run_idempotent(
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
            raise WebhookProcessingError(msg)
        return ApiResponse(data=cached)
