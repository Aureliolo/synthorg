# module-kind: controller
"""Webhook receipt retry endpoint."""

from litestar import Controller, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.api_core_state import idempotency_service_of
from synthorg.api.controllers.webhooks._retry_helpers import (
    _assert_receipt_retryable,
    _load_payload_from_receipt,
    _retry_publish_and_transition,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_write_access
from synthorg.api.path_params import PathId
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT
from synthorg.observability.events.integrations import WEBHOOK_RECEIPT_NOT_FOUND
from synthorg.persistence.state import persistence_of

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

        Heavy lifting lives in module-level helpers
        (``_load_payload_from_receipt``, ``_assert_receipt_retryable``,
        ``_transition_webhook_receipt_status``) so this orchestrator
        stays under the repository's 50-line function cap.

        Returns:
            ``ApiResponse[dict[str, object]]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            ConflictError: Raised on the corresponding failure path.
            TypeError: Raised on the corresponding failure path.
        """
        persistence = persistence_of(state["app_state"])
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
            _assert_receipt_retryable(receipt)
            payload = _load_payload_from_receipt(receipt)
            return await _retry_publish_and_transition(
                persistence=persistence,
                bus=bus,
                receipt=receipt,
                payload=dict(payload),
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
            raise TypeError(msg)
        return ApiResponse(data=cached)
