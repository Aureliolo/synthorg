"""A2A external gateway event constants.

Events covering gateway lifecycle, inbound/outbound requests,
Agent Card operations, peer management, and security checks.
"""

from typing import Final

# -- Gateway lifecycle -----------------------------------------------------

A2A_GATEWAY_STARTED: Final[str] = "a2a.gateway.started"
A2A_GATEWAY_STOPPED: Final[str] = "a2a.gateway.stopped"
A2A_GATEWAY_DISABLED: Final[str] = "a2a.gateway.disabled"

# -- Inbound requests ------------------------------------------------------

A2A_INBOUND_RECEIVED: Final[str] = "a2a.inbound.received"
A2A_INBOUND_DISPATCHED: Final[str] = "a2a.inbound.dispatched"
A2A_INBOUND_REJECTED: Final[str] = "a2a.inbound.rejected"
A2A_INBOUND_AUTH_FAILED: Final[str] = "a2a.inbound.auth_failed"
A2A_INBOUND_PEER_NOT_ALLOWED: Final[str] = "a2a.inbound.peer_not_allowed"
A2A_INBOUND_PAYLOAD_TOO_LARGE: Final[str] = "a2a.inbound.payload_too_large"
A2A_INBOUND_RATE_LIMITED: Final[str] = "a2a.inbound.rate_limited"

# -- Outbound requests -----------------------------------------------------

A2A_OUTBOUND_SENT: Final[str] = "a2a.outbound.sent"
A2A_OUTBOUND_FAILED: Final[str] = "a2a.outbound.failed"
A2A_OUTBOUND_RESPONSE_INVALID: Final[str] = "a2a.outbound.response_invalid"
A2A_OUTBOUND_SSRF_BLOCKED: Final[str] = "a2a.outbound.ssrf_blocked"

# -- Agent Card operations -------------------------------------------------

A2A_AGENT_CARD_SERVED: Final[str] = "a2a.agent_card.served"
A2A_AGENT_CARD_BUILT: Final[str] = "a2a.agent_card.built"
A2A_AGENT_CARD_NOT_FOUND: Final[str] = "a2a.agent_card.not_found"
A2A_AGENT_CARD_CACHE_HIT: Final[str] = "a2a.agent_card.cache_hit"
A2A_AGENT_CARD_CACHE_MISS: Final[str] = "a2a.agent_card.cache_miss"

# -- Task operations -------------------------------------------------------

A2A_TASK_CREATED: Final[str] = "a2a.task.created"
A2A_TASK_STATE_CHANGED: Final[str] = "a2a.task.state_changed"
A2A_TASK_CANCELLED: Final[str] = "a2a.task.cancelled"
A2A_TASK_NOT_FOUND: Final[str] = "a2a.task.not_found"
A2A_TASK_METHOD_REJECTED: Final[str] = "a2a.task.method_rejected"

# -- SSE streaming ---------------------------------------------------------

A2A_STREAM_STARTED: Final[str] = "a2a.stream.started"
A2A_STREAM_EVENT_SENT: Final[str] = "a2a.stream.event_sent"
A2A_STREAM_IDLE_TIMEOUT: Final[str] = "a2a.stream.idle_timeout"
A2A_STREAM_CLOSED: Final[str] = "a2a.stream.closed"

# -- Peer management -------------------------------------------------------

A2A_PEER_REGISTERED: Final[str] = "a2a.peer.registered"
A2A_PEER_REMOVED: Final[str] = "a2a.peer.removed"
A2A_PEER_DISCOVERED: Final[str] = "a2a.peer.discovered"

# -- Push notifications ----------------------------------------------------

A2A_PUSH_RECEIVED: Final[str] = "a2a.push.received"
A2A_PUSH_VERIFIED: Final[str] = "a2a.push.verified"
A2A_PUSH_VERIFICATION_FAILED: Final[str] = "a2a.push.verification_failed"
A2A_PUSH_VERIFIER_CONFIG_INVALID: Final[str] = "a2a.push.verifier_config_invalid"

# -- JSON-RPC errors -------------------------------------------------------

A2A_JSONRPC_PARSE_ERROR: Final[str] = "a2a.jsonrpc.parse_error"
A2A_JSONRPC_METHOD_NOT_FOUND: Final[str] = "a2a.jsonrpc.method_not_found"
A2A_JSONRPC_INVALID_PARAMS: Final[str] = "a2a.jsonrpc.invalid_params"

# Server-side ``message/send`` idempotency-guard rejections. Kept distinct
# from A2A_JSONRPC_INVALID_PARAMS (which is for client param-validation
# failures) so operator telemetry separates "the caller sent bad params"
# from "the server's durable idempotency guard refused the call". The
# ``reason`` kwarg differentiates the in-flight retry from a corrupt cache.
A2A_MESSAGE_SEND_IDEMPOTENCY_REJECTED: Final[str] = (
    "a2a.message_send.idempotency_rejected"
)
