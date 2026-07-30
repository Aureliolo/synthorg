# module-kind: controller
"""Webhook ingest endpoint -- receive, verify, dedup, publish."""

import json

from litestar import Controller, Request, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.controllers._webhooks_wiring import (
    WebhookEventPayload,
    build_delivery_key,
)
from synthorg.api.controllers.webhooks._authentication import (
    get_verified_connection,
    read_delivery_id,
    verify_signature,
)
from synthorg.api.controllers.webhooks._shared import (
    _check_replay_or_freshness,
    _enforce_max_payload,
    _parse_timestamp,
    _publish_with_durable_idempotency,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.path_params import PathEventType, PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.boundary import parse_typed
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
        bus publication. Returns 202 Accepted on success.

        Every rejection before the delivery authenticates answers 401 with one
        message, whether the connection is unknown, the type has no verifier, the
        secret is unset, or the signature did not match. This endpoint takes no
        credentials, so distinguishing them would let an unauthenticated caller
        enumerate connection names and probe their configuration; the reason is
        kept in the structured log instead. A malformed timestamp is 400 and a
        duplicate delivery 409, neither of which reveals anything a caller that
        already holds a valid signature does not know.

        Returns:
            ``ApiResponse[dict[str, object]]`` instance.

        Raises:
            UnauthorizedError: If the delivery cannot be authenticated.
            ConflictError: If replay or freshness validation fails.
            ValidationError: Raised on the corresponding failure path.
        """
        catalog = require_service(
            state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
            "Connection Catalog",
        )
        conn = await get_verified_connection(state, connection_name)
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

        await verify_signature(
            catalog=catalog,
            connection=conn,
            body=body,
            headers=headers,
        )

        # One identity for both dedup gates: this connection, these bytes. The
        # digest is what the signature just covered, and it is always present,
        # so the replay gate never fails closed for want of a freshness signal,
        # which is what rejected every genuine delivery (no provider sends the
        # generic ``X-Nonce`` / ``X-Request-Id`` / ``X-Timestamp`` headers this
        # path used to look for on its own). Nothing outside the signature is in
        # it, so no header value and no URL ``event_type`` can widen it.
        dedup_key = build_delivery_key(connection_name=connection_name, body=body)
        # Recorded for traceability only: each provider names its own delivery
        # id (``X-GitHub-Delivery`` and friends), which the verifier declares.
        delivery_id = read_delivery_id(headers, conn.connection_type)
        timestamp = _parse_timestamp(headers, connection_name=connection_name)
        await _check_replay_or_freshness(
            state=state,
            connection_name=connection_name,
            dedup_key=dedup_key,
            delivery_id=delivery_id,
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

        # The same key the in-memory gate just asserted on, so the durable
        # service agrees with it in both directions: a provider retry of a
        # byte-identical delivery collapses onto the cached 202 on any replica,
        # and a replay cannot mint a second publish by dressing up its headers or
        # posting the same bytes to a different event name.
        cached = await _publish_with_durable_idempotency(
            state=state,
            connection_name=connection_name,
            event_type=event_type,
            delivery_key=dedup_key,
            connection_type=conn.connection_type,
            bus=bus,
            payload=normalized_payload,
            dedup_source="body_sha256",
        )
        return ApiResponse(data=cached)
