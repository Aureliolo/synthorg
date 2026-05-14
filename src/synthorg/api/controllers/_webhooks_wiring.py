"""Boundary types and lazy-wired services for the webhooks controller.

Three groups of helpers live here so the controller module stays focused
on request handling:

* :class:`WebhookEventPayload`: the typed Pydantic boundary for inbound
  webhook bodies. The envelope must be a JSON object; anything else is
  rejected at ``parse_typed`` time.
* :func:`_build_idem_scope` / :func:`_build_idem_key`: pure functions
  that compose the durable idempotency-key for a webhook delivery.
* :func:`_get_activity_service` / :func:`_get_replay_protector`: lazy
  accessors cached on ``app_state``. Both serialise the construct-and-
  store step through an :class:`asyncio.Lock` so two concurrent first
  requests cannot both build an instance and have the second silently
  overwrite the first. The lost-write is especially harmful for the
  replay protector, whose in-process nonce cache is the source of
  truth between durable-idempotency reads.
"""

import asyncio
import hashlib
from typing import Final

from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict

from synthorg.integrations.webhooks.activity_service import WebhookActivityService
from synthorg.integrations.webhooks.replay_protection import (
    MAX_NONCE_CHARS,
    ReplayProtector,
)


class WebhookEventPayload(BaseModel):
    """Typed boundary for an incoming webhook event payload.

    The wire shape is provider-defined (each external service sends
    arbitrary keys), so the model uses ``extra="allow"`` to accept the
    full key set unchanged. The contract this model enforces is the
    *envelope shape*: the payload MUST be a JSON object (not an array,
    scalar, or non-JSON body). That envelope check is the gate that
    closes the silent ``{"raw": ...}`` fallback the controller used
    before. Do not flip this to ``extra="forbid"``: it would break
    every external provider integration.
    """

    model_config = ConfigDict(frozen=True, extra="allow")


# DB CHECK constraint on ``idempotency_keys.key`` caps the column at
# 255 chars. The composed key from connection_name + event_type +
# attacker-controlled nonce can exceed that; we collapse to a fixed
# SHA-256 digest when oversized so the DB insert never fails on
# length while operator-visible logs still carry the route prefix.
_IDEMPOTENCY_KEY_MAX_LEN: Final[int] = 255


def _build_idem_scope(
    *,
    connection_type: str,
    connection_name: str,
) -> str:
    """Compose the durable idempotency scope for a webhook connection.

    Including ``connection_name`` (and not just ``connection_type``) is
    defence in depth at the persistence row level: a stale dedup row
    written under one connection can never surface to a different
    connection that happens to share an event type and nonce.
    """
    return f"webhooks:{connection_type}:{connection_name}"


def _build_idem_key(
    *,
    connection_name: str,
    event_type: str,
    nonce: str,
) -> str:
    """Compose the durable idempotency key with bounded length.

    Two-step bounding: (1) fingerprint the nonce up front when it
    exceeds ``MAX_NONCE_CHARS`` so an attacker cannot force
    unbounded string copies into the f-string; (2) collapse the
    composed key via SHA-256 if it still exceeds the DB column's
    255-char CHECK constraint, preserving the (connection, event)
    prefix for operator visibility when possible.
    """
    nonce_for_key = (
        nonce
        if len(nonce) <= MAX_NONCE_CHARS
        else hashlib.sha256(
            nonce.encode("utf-8", errors="replace"),
        ).hexdigest()
    )
    raw_key = f"{connection_name}:{event_type}:{nonce_for_key}"
    if len(raw_key) > _IDEMPOTENCY_KEY_MAX_LEN:
        nonce_digest = hashlib.sha256(
            nonce_for_key.encode("utf-8", errors="replace"),
        ).hexdigest()
        raw_key = f"{connection_name}:{event_type}:sha256:{nonce_digest}"
        if len(raw_key) > _IDEMPOTENCY_KEY_MAX_LEN:
            raw_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return raw_key


# Module-level locks serialise the construct-and-store half of the
# lazy-init helpers below. They are created without a running event
# loop and bind to one on first use, which is safe under Python 3.10+.
_activity_service_lock: Final[asyncio.Lock] = asyncio.Lock()
_replay_protector_lock: Final[asyncio.Lock] = asyncio.Lock()


async def _get_activity_service(state: State) -> WebhookActivityService:
    """Return (and lazily build) the read-only webhook activity service.

    The service holds a reference to the persistence-backed
    :class:`WebhookReceiptRepository` so the controller body never
    touches ``persistence.webhook_receipts`` directly. The cache lives
    on ``app_state`` so a single instance survives across requests.
    The lock guards against concurrent first-call races.
    """
    app_state = state["app_state"]
    cached: WebhookActivityService | None = getattr(
        app_state,
        "_webhook_activity_service",
        None,
    )
    if cached is not None:
        return cached
    async with _activity_service_lock:
        cached = getattr(app_state, "_webhook_activity_service", None)
        if cached is None:
            cached = WebhookActivityService(
                receipts_repo=app_state.persistence.webhook_receipts,
            )
            app_state._webhook_activity_service = cached  # noqa: SLF001
        return cached


async def _get_replay_protector(state: State) -> ReplayProtector:
    """Return (and lazily build) a config-driven ``ReplayProtector``.

    The protector instance is cached on ``app_state`` so the nonce
    cache persists across requests, but is constructed from
    ``integrations.webhooks.replay_window_seconds`` at first use
    instead of being frozen at module-import time. Two concurrent
    first requests must not both construct a protector: the second
    write would discard the nonces the first had already seen, briefly
    weakening replay protection. The lock makes the construct-and-
    store atomic.
    """
    app_state = state["app_state"]
    cached: ReplayProtector | None = getattr(
        app_state,
        "_webhook_replay_protector",
        None,
    )
    if cached is not None:
        return cached
    async with _replay_protector_lock:
        cached = getattr(app_state, "_webhook_replay_protector", None)
        if cached is None:
            cfg = app_state.config.integrations.webhooks
            cached = ReplayProtector(
                window_seconds=cfg.replay_window_seconds,
                max_entries=10_000,
            )
            app_state._webhook_replay_protector = cached  # noqa: SLF001
        return cached
