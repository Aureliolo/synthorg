# module-kind: controller
"""Webhook ingest endpoint -- receive, verify, dedup, publish."""

import hashlib
import json

from litestar import Controller, Request, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.boundary import parse_typed
from synthorg.api.controllers._webhooks_wiring import WebhookEventPayload
from synthorg.api.controllers.webhooks._shared import (
    _check_replay_or_freshness,
    _enforce_max_payload,
    _get_connection_or_404,
    _parse_timestamp,
    _publish_with_durable_idempotency,
    _verify_signature,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.path_params import PathEventType, PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.domain_errors import ValidationError
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_VALIDATION_FAILED
from synthorg.observability.events.integrations import WEBHOOK_RECEIVED

logger = get_logger(__name__)


class WebhooksIngestController(Controller):
    """Webhook receiver endpoint."""

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
        request: Request[object, object, State],
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

        Returns:
            ``ApiResponse[dict[str, object]]`` instance.

        Raises:
            NotFoundError: If the named connection does not exist.
            UnauthorizedError: If the signature is missing or invalid.
            ConflictError: If replay or freshness validation fails.
            ValidationError: Raised on the corresponding failure path.
        """
        catalog = require_service(
            state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
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

        bus = require_service(
            state["app_state"].slice(CommunicationStateSlice).message_bus,
            "Message Bus",
        )

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
