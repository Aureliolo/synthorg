"""Webhooks API controller.

Receives webhook events from external services, verifies
signatures, and publishes to the message bus.
"""

import hashlib
import json
from typing import Any

from litestar import Controller, Request, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.dto import ApiResponse
from synthorg.api.errors import (
    ApiValidationError,
    ConflictError,
    NotFoundError,
    UnauthorizedError,
)
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.integrations.connections.models import WebhookReceipt  # noqa: TC001
from synthorg.integrations.webhooks.event_bus_bridge import (
    publish_webhook_event,
)
from synthorg.integrations.webhooks.replay_protection import ReplayProtector
from synthorg.integrations.webhooks.verifiers.factory import get_verifier
from synthorg.observability import get_logger
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT
from synthorg.observability.events.integrations import (
    WEBHOOK_ACCEPTED,
    WEBHOOK_RECEIVED,
    WEBHOOK_REJECTED,
)

logger = get_logger(__name__)

# DB CHECK constraint on ``idempotency_keys.key`` caps the column at
# 255 chars. The composed key from connection_name + event_type +
# attacker-controlled nonce can exceed that; we collapse to a fixed
# SHA-256 digest when oversized so the DB insert never fails on
# length while operator-visible logs still carry the route prefix.
_IDEMPOTENCY_KEY_MAX_LEN: int = 255


def _get_replay_protector(state: State) -> ReplayProtector:
    """Return (and lazily build) a config-driven ``ReplayProtector``.

    The protector instance is cached on ``app_state`` so the nonce
    cache persists across requests, but is constructed from
    ``integrations.webhooks.replay_window_seconds`` at first use
    instead of being frozen at module-import time. That way runtime
    config overrides actually change receiver behaviour.
    """
    app_state = state["app_state"]
    cached = getattr(app_state, "_webhook_replay_protector", None)
    if cached is None:
        cfg = app_state.config.integrations.webhooks
        cached = ReplayProtector(
            window_seconds=cfg.replay_window_seconds,
            max_entries=10_000,
        )
        app_state._webhook_replay_protector = cached  # noqa: SLF001
    return cached


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
    async def receive_webhook(  # noqa: C901, PLR0912, PLR0915
        self,
        state: State,
        request: Request[Any, Any, Any],
        connection_name: str,
        event_type: str,
    ) -> ApiResponse[dict[str, object]]:
        """Receive and verify a webhook event.

        Returns 202 Accepted on success. Raises structured errors
        (404 on unknown connection, 401 on missing or failed
        signature, 400 on malformed timestamp, 409 on replay).
        """
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

        logger.info(
            WEBHOOK_RECEIVED,
            connection_name=connection_name,
            event_type=event_type,
        )

        # Enforce ``integrations.webhooks.max_payload_bytes`` before
        # buffering. ``request.body()`` pulls the full payload into
        # memory, so a missing cap lets an attacker DoS the process
        # with oversized posts even when the app-wide 50 MB default
        # still applies.
        webhook_cfg = state["app_state"].config.integrations.webhooks
        max_payload = webhook_cfg.max_payload_bytes
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
                raise ApiValidationError(msg) from None
            if content_length > max_payload:
                logger.warning(
                    WEBHOOK_REJECTED,
                    connection_name=connection_name,
                    reason="content-length exceeds max_payload_bytes",
                    content_length=content_length,
                    max_payload=max_payload,
                )
                msg = (
                    f"Webhook payload exceeds configured "
                    f"max_payload_bytes ({max_payload})"
                )
                raise ApiValidationError(msg)

        body = await request.body()
        if len(body) > max_payload:
            logger.warning(
                WEBHOOK_REJECTED,
                connection_name=connection_name,
                reason="body exceeds max_payload_bytes",
                body_length=len(body),
                max_payload=max_payload,
            )
            msg = (
                f"Webhook payload exceeds configured max_payload_bytes ({max_payload})"
            )
            raise ApiValidationError(msg)
        headers = {k.lower(): v for k, v in request.headers.items()}

        # Signature verification -- fail closed when secret missing.
        verifier = get_verifier(conn.connection_type)
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

        # Replay protection -- parse timestamp defensively.
        nonce = headers.get("x-nonce") or headers.get("x-request-id")
        timestamp_str = headers.get("x-timestamp", "")
        timestamp: float | None = None
        if timestamp_str:
            try:
                timestamp = float(timestamp_str)
            except ValueError:
                logger.warning(
                    WEBHOOK_REJECTED,
                    connection_name=connection_name,
                    reason="malformed x-timestamp header",
                )
                msg = "Malformed x-timestamp header"
                raise ApiValidationError(msg) from None

        replay_protector = _get_replay_protector(state)
        if nonce:
            # Durable IdempotencyService below handles dedup across
            # processes / restarts; the in-memory nonce cache would
            # otherwise reject a legitimate retry with the same nonce
            # (different replica or after a restart) before the
            # durable claim can return the cached response. We still
            # validate timestamp staleness here so an attacker with a
            # captured signature cannot replay outside the freshness
            # window even on a fresh nonce.
            if not replay_protector.check_freshness(timestamp):
                logger.warning(
                    WEBHOOK_REJECTED,
                    connection_name=connection_name,
                    reason="stale timestamp",
                )
                msg = "Replay detected (stale timestamp)"
                raise ConflictError(msg)
        elif not replay_protector.check(nonce=nonce, timestamp=timestamp):
            logger.warning(
                WEBHOOK_REJECTED,
                connection_name=connection_name,
                reason="replay detected",
            )
            msg = "Replay detected (duplicate nonce or stale timestamp)"
            raise ConflictError(msg)

        # Parse payload (best-effort -- unparseable stays raw).
        try:
            payload = json.loads(body)
        except json.JSONDecodeError, UnicodeDecodeError:
            payload = {"raw": body.decode("utf-8", errors="replace")}

        bus = state["app_state"].message_bus
        normalized_payload = payload if isinstance(payload, dict) else {"data": payload}

        # Persistent idempotency: if the request carried a nonce, scope
        # to (connection_type, nonce) so a retry hitting a different
        # process replica still short-circuits to the cached response
        # instead of double-publishing to the bus. Requests without a
        # nonce skip the persistent check (the in-memory ReplayProtector
        # has already enforced its window above).
        if nonce:
            from synthorg.core.types import NotBlankStr  # noqa: PLC0415

            scope = NotBlankStr(f"webhooks:{conn.connection_type}")
            # Include event_type in the durable key so two distinct
            # routes that legitimately share a (connection_name, nonce)
            # tuple (different event subscriptions on the same upstream)
            # don't collide and replay each other's cached responses.
            # If the composed key exceeds the DB column's 255-char
            # CHECK constraint (an attacker-controllable nonce can be
            # arbitrarily long), collapse the nonce into a SHA-256
            # digest while preserving the (connection_name, event_type)
            # prefix so operator-visible logs still carry the route.
            raw_key = f"{connection_name}:{event_type}:{nonce}"
            if len(raw_key) > _IDEMPOTENCY_KEY_MAX_LEN:
                nonce_digest = hashlib.sha256(
                    nonce.encode("utf-8", errors="replace"),
                ).hexdigest()
                raw_key = f"{connection_name}:{event_type}:sha256:{nonce_digest}"
                # Defensive truncation in case the connection_name +
                # event_type prefix alone is also pathologically long.
                if len(raw_key) > _IDEMPOTENCY_KEY_MAX_LEN:
                    raw_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
            idem_key = NotBlankStr(raw_key)

            async def _publish_and_accept() -> dict[str, object]:
                await publish_webhook_event(
                    bus=bus,
                    connection_name=connection_name,
                    event_type=event_type,
                    payload=normalized_payload,
                )
                logger.info(
                    WEBHOOK_ACCEPTED,
                    connection_name=connection_name,
                    event_type=event_type,
                )
                return {"status": "accepted", "event_type": event_type}

            cached, _fresh = await state[
                "app_state"
            ].idempotency_service.run_idempotent(
                scope=scope,
                key=idem_key,
                callback=_publish_and_accept,
            )
            if cached is None:
                # Durable claim couldn't resolve in the polling window
                # -- record the contention before raising 409 so the
                # operator-visible failure trail carries the
                # connection / event / idempotency-key context the
                # bare ConflictError body does not.
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
            return ApiResponse(data=cached)

        await publish_webhook_event(
            bus=bus,
            connection_name=connection_name,
            event_type=event_type,
            payload=normalized_payload,
        )
        logger.info(
            WEBHOOK_ACCEPTED,
            connection_name=connection_name,
            event_type=event_type,
        )
        return ApiResponse(
            data={"status": "accepted", "event_type": event_type},
        )

    @get(
        "/{connection_name:str}/activity",
        guards=[require_read_access],
        summary="List webhook activity for a connection",
    )
    async def list_activity(
        self,
        state: State,
        connection_name: str,
        limit: int = Parameter(
            default=100,
            ge=1,
            le=500,
            description="Max results",
        ),
    ) -> ApiResponse[tuple[WebhookReceipt, ...]]:
        """List recent webhook receipts for a connection."""
        persistence = state["app_state"].persistence
        receipts = await persistence.webhook_receipts.get_by_connection(
            connection_name,
            limit=limit,
        )
        return ApiResponse(data=receipts)
