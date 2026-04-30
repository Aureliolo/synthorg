"""Integration subsystem event constants.

Events covering connection lifecycle, secret management, OAuth flows,
webhook receipt and verification, health checks, rate limiting, MCP
catalog operations, and tunnel lifecycle.
"""

from typing import Final

# -- Connection lifecycle ------------------------------------------------

CONNECTION_CREATED: Final[str] = "integrations.connection.created"
CONNECTION_UPDATED: Final[str] = "integrations.connection.updated"
CONNECTION_DELETED: Final[str] = "integrations.connection.deleted"
CONNECTION_NOT_FOUND: Final[str] = "integrations.connection.not_found"
CONNECTION_DUPLICATE: Final[str] = "integrations.connection.duplicate"
CONNECTION_VALIDATION_FAILED: Final[str] = "integrations.connection.validation_failed"
CONNECTION_AUTHENTICATOR_MISSING: Final[str] = (
    "integrations.connection.authenticator_missing"
)

# -- Secret management ---------------------------------------------------

SECRET_STORED: Final[str] = "integrations.secret.stored"  # noqa: S105
SECRET_RETRIEVED: Final[str] = "integrations.secret.retrieved"  # noqa: S105
SECRET_DELETED: Final[str] = "integrations.secret.deleted"  # noqa: S105
SECRET_ROTATED: Final[str] = "integrations.secret.rotated"  # noqa: S105
SECRET_RETRIEVAL_FAILED: Final[str] = "integrations.secret.retrieval_failed"  # noqa: S105
SECRET_STORAGE_FAILED: Final[str] = "integrations.secret.storage_failed"  # noqa: S105
SECRET_DELETE_FAILED: Final[str] = "integrations.secret.delete_failed"  # noqa: S105
SECRET_BACKEND_UNAVAILABLE: Final[str] = "integrations.secret.backend_unavailable"  # noqa: S105

# -- OAuth flow ----------------------------------------------------------

OAUTH_FLOW_STARTED: Final[str] = "integrations.oauth.flow_started"
OAUTH_FLOW_COMPLETED: Final[str] = "integrations.oauth.flow_completed"
OAUTH_FLOW_FAILED: Final[str] = "integrations.oauth.flow_failed"
OAUTH_CALLBACK_RECEIVED: Final[str] = "integrations.oauth.callback_received"
OAUTH_STATE_INVALID: Final[str] = "integrations.oauth.state_invalid"
OAUTH_TOKEN_EXCHANGED: Final[str] = "integrations.oauth.token_exchanged"  # noqa: S105
OAUTH_TOKEN_EXCHANGE_FAILED: Final[str] = "integrations.oauth.token_exchange_failed"  # noqa: S105
OAUTH_TOKEN_REFRESHED: Final[str] = "integrations.oauth.token_refreshed"  # noqa: S105
OAUTH_TOKEN_REFRESH_FAILED: Final[str] = "integrations.oauth.token_refresh_failed"  # noqa: S105
OAUTH_TOKEN_EXPIRED: Final[str] = "integrations.oauth.token_expired"  # noqa: S105
OAUTH_DEVICE_FLOW_STARTED: Final[str] = "integrations.oauth.device_flow_started"
OAUTH_DEVICE_FLOW_POLLING: Final[str] = "integrations.oauth.device_flow_polling"
OAUTH_DEVICE_FLOW_GRANTED: Final[str] = "integrations.oauth.device_flow_granted"
OAUTH_DEVICE_FLOW_TIMEOUT: Final[str] = "integrations.oauth.device_flow_timeout"
OAUTH_PKCE_VALIDATION_FAILED: Final[str] = "integrations.oauth.pkce_validation_failed"

# -- Webhook reception ---------------------------------------------------

WEBHOOK_RECEIVED: Final[str] = "integrations.webhook.received"
WEBHOOK_SIGNATURE_VERIFIED: Final[str] = "integrations.webhook.signature_verified"
WEBHOOK_SIGNATURE_INVALID: Final[str] = "integrations.webhook.signature_invalid"
WEBHOOK_REPLAY_DETECTED: Final[str] = "integrations.webhook.replay_detected"
WEBHOOK_ACCEPTED: Final[str] = "integrations.webhook.accepted"
WEBHOOK_REJECTED: Final[str] = "integrations.webhook.rejected"
WEBHOOK_EVENT_PUBLISHED: Final[str] = "integrations.webhook.event_published"
WEBHOOK_EVENT_PUBLISH_FAILED: Final[str] = "integrations.webhook.event_publish_failed"
WEBHOOK_RATE_LIMITED: Final[str] = "integrations.webhook.rate_limited"
WEBHOOK_RECEIPT_LOGGED: Final[str] = "integrations.webhook.receipt_logged"
WEBHOOK_VERIFIER_UNSUPPORTED_TYPE: Final[str] = (
    "integrations.webhook.verifier_unsupported_type"
)

# -- Health checks -------------------------------------------------------

HEALTH_CHECK_STARTED: Final[str] = "integrations.health.check_started"
HEALTH_CHECK_PASSED: Final[str] = "integrations.health.check_passed"
HEALTH_CHECK_FAILED: Final[str] = "integrations.health.check_failed"
HEALTH_PROBER_STARTED: Final[str] = "integrations.health.prober_started"
HEALTH_PROBER_STOPPED: Final[str] = "integrations.health.prober_stopped"
HEALTH_PROBER_CONFIG_INVALID: Final[str] = "integrations.health.prober_config_invalid"
HEALTH_STATUS_CHANGED: Final[str] = "integrations.health.status_changed"

# -- Tool-side rate limiting ---------------------------------------------

TOOL_RATE_LIMIT_ACQUIRED: Final[str] = "integrations.rate_limit.acquired"
TOOL_RATE_LIMIT_HIT: Final[str] = "integrations.rate_limit.hit"
TOOL_RATE_LIMIT_WAIT: Final[str] = "integrations.rate_limit.wait"
TOOL_RATE_LIMIT_BACKOFF: Final[str] = "integrations.rate_limit.backoff"

# -- MCP catalog ---------------------------------------------------------

MCP_CATALOG_BROWSED: Final[str] = "integrations.mcp_catalog.browsed"
MCP_CATALOG_ENTRY_NOT_FOUND: Final[str] = "integrations.mcp_catalog.entry_not_found"
MCP_SERVER_INSTALLED: Final[str] = "integrations.mcp_catalog.installed"
MCP_SERVER_UNINSTALLED: Final[str] = "integrations.mcp_catalog.uninstalled"
MCP_SERVER_UNINSTALL_NOOP: Final[str] = "integrations.mcp_catalog.uninstall_noop"
MCP_SERVER_INSTALL_FAILED: Final[str] = "integrations.mcp_catalog.install_failed"
MCP_SERVER_INSTALL_VALIDATION_FAILED: Final[str] = (
    "integrations.mcp_catalog.install_validation_failed"
)
CONNECTION_SECRET_REVEALED: Final[str] = "integrations.connection.secret_revealed"  # noqa: S105
CONNECTION_SECRET_REVEAL_FAILED: Final[str] = (
    "integrations.connection.secret_reveal_failed"  # noqa: S105
)

# -- Tunnel --------------------------------------------------------------

TUNNEL_STARTED: Final[str] = "integrations.tunnel.started"
TUNNEL_STOPPED: Final[str] = "integrations.tunnel.stopped"
TUNNEL_ERROR: Final[str] = "integrations.tunnel.error"

# -- Webhook bridge ------------------------------------------------------

WEBHOOK_BRIDGE_STARTED: Final[str] = "integrations.webhook_bridge.started"
WEBHOOK_BRIDGE_STOPPED: Final[str] = "integrations.webhook_bridge.stopped"
WEBHOOK_BRIDGE_POLL_ERROR: Final[str] = "integrations.webhook_bridge.poll_error"
WEBHOOK_BRIDGE_EVENT_FORWARDED: Final[str] = (
    "integrations.webhook_bridge.event_forwarded"
)

# -- Rate limit coordination ---------------------------------------------

RATE_LIMIT_ACQUIRE_PUBLISHED: Final[str] = "integrations.rate_limit.acquire_published"
RATE_LIMIT_COORDINATOR_STARTED: Final[str] = (
    "integrations.rate_limit.coordinator_started"
)
RATE_LIMIT_COORDINATOR_STOPPED: Final[str] = (
    "integrations.rate_limit.coordinator_stopped"
)

# -- Provider migration --------------------------------------------------

PROVIDER_CONNECTION_RESOLVED: Final[str] = "integrations.provider.connection_resolved"
PROVIDER_CONNECTION_RESOLUTION_FAILED: Final[str] = (
    "integrations.provider.connection_resolution_failed"
)

# -- MCP audit events ----------------------------------------------------

MCP_CATALOG_INSTALLED_VIA_MCP: Final[str] = "integrations.mcp_catalog.installed_via_mcp"
MCP_CATALOG_UNINSTALLED_VIA_MCP: Final[str] = (
    "integrations.mcp_catalog.uninstalled_via_mcp"
)
OAUTH_PROVIDER_CONFIGURED_VIA_MCP: Final[str] = (
    "integrations.oauth.provider_configured_via_mcp"
)
OAUTH_PROVIDER_REMOVED_VIA_MCP: Final[str] = (
    "integrations.oauth.provider_removed_via_mcp"
)
CLIENT_CREATED_VIA_MCP: Final[str] = "integrations.client.created_via_mcp"
CLIENT_DEACTIVATED_VIA_MCP: Final[str] = "integrations.client.deactivated_via_mcp"
ARTIFACT_CREATED_VIA_MCP: Final[str] = "integrations.artifact.created_via_mcp"
ARTIFACT_DELETED_VIA_MCP: Final[str] = "integrations.artifact.deleted_via_mcp"
