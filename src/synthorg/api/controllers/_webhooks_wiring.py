"""Boundary types and idempotency-key helpers for the webhooks controller.

Two groups of helpers live here so the controller module stays focused
on request handling:

* :class:`WebhookEventPayload`: the typed Pydantic boundary for inbound
  webhook bodies. The envelope must be a JSON object; anything else is
  rejected at ``parse_typed`` time.
* :func:`_build_idem_scope` / :func:`_build_idem_key`: pure functions
  that compose the durable idempotency-key for a webhook delivery.

The webhook activity service and replay protector are no longer built
lazily here; they are wired once at startup
(``_wire_webhook_request_services``) onto ``IntegrationsStateSlice`` and
read through ``webhook_activity_service_of`` /
``webhook_replay_protector_of`` so the controller never touches
``persistence.webhook_receipts`` directly. The replay protector's
in-process nonce cache is the source of truth between durable-
idempotency reads, so a single wired instance must serve every request.
"""

import hashlib
from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.integrations.webhooks.replay_protection import MAX_NONCE_CHARS


class WebhookEventPayload(
    BaseModel
):  # lint-allow: frozen-extra-forbid -- external webhook providers send arbitrary keys; envelope-only validation uses extra="allow" by design (docs/reference/typed-boundaries.md)  # noqa: E501
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="allow")


# DB CHECK constraint on ``idempotency_keys.key`` caps the column at
# 255 chars. The composed key from connection_name + event_type +
# attacker-controlled nonce can exceed that; we collapse to a fixed
# SHA-256 digest when oversized so the DB insert never fails on
# length while operator-visible logs still carry the route prefix.
_IDEMPOTENCY_KEY_MAX_LEN: Final[int] = 255

# In-process LRU cap for the ``ReplayProtector`` nonce cache. Chosen
# to bound memory under a flood of unique nonces (each entry is the
# nonce string plus an int timestamp; 10_000 entries is well below
# the GiB scale even with kilobyte-sized nonces) while staying large
# enough to cover the longest realistic replay window at a sustained
# webhook delivery rate.
_REPLAY_PROTECTOR_MAX_ENTRIES: Final[int] = 10_000


def _len_prefixed(segment: str) -> str:
    """Length-prefix a segment for injective string composition.

    Joining raw segments with ``:`` is ambiguous when a segment itself
    contains ``:``: ``("a:b", "c")`` and ``("a", "b:c")`` both collapse
    to ``"a:b:c"``. Prefixing every segment with its character length
    makes the encoding injective -- the same tuples encode as
    ``"3:a:b:1:c"`` and ``"1:a:3:b:c"`` respectively. The colon inside
    a segment is irrelevant because the length tells the reader (and
    any future parser) how many characters belong to that segment.

    Returns:
        Resulting string.
    """
    return f"{len(segment)}:{segment}"


def _build_idem_scope(
    *,
    connection_type: str,
    connection_name: str,
) -> str:
    """Compose the durable idempotency scope for a webhook connection.

    Including ``connection_name`` (and not just ``connection_type``) is
    defence in depth at the persistence row level: a stale dedup row
    written under one connection can never surface to a different
    connection that happens to share an event type and nonce. Each
    segment is length-prefixed via :func:`_len_prefixed` so two
    distinct ``(connection_type, connection_name)`` pairs can never
    collapse to the same scope string even when one of the parts
    contains ``":"``.

    Returns:
        Resulting string.
    """
    return f"webhooks:{_len_prefixed(connection_type)}:{_len_prefixed(connection_name)}"


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
    prefix for operator visibility when possible. Each input segment
    is length-prefixed via :func:`_len_prefixed` so two distinct
    ``(connection_name, event_type, nonce)`` tuples can never produce
    the same key string even when one of the parts contains ``":"``.

    Returns:
        Resulting string.
    """
    nonce_for_key = (
        nonce
        if len(nonce) <= MAX_NONCE_CHARS
        else hashlib.sha256(
            nonce.encode("utf-8", errors="replace"),
        ).hexdigest()
    )
    encoded_name = _len_prefixed(connection_name)
    encoded_event = _len_prefixed(event_type)
    raw_key = f"{encoded_name}:{encoded_event}:{_len_prefixed(nonce_for_key)}"
    if len(raw_key) > _IDEMPOTENCY_KEY_MAX_LEN:
        nonce_digest = hashlib.sha256(
            nonce_for_key.encode("utf-8", errors="replace"),
        ).hexdigest()
        raw_key = f"{encoded_name}:{encoded_event}:sha256:{nonce_digest}"
        if len(raw_key) > _IDEMPOTENCY_KEY_MAX_LEN:
            raw_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return raw_key
