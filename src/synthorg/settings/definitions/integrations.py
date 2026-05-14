"""Integrations namespace setting definitions.

Covers health probing of external connections, OAuth flow HTTP
timeouts, and rate-limit coordinator polling.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="health_probe_interval_seconds",
        type=SettingType.INTEGER,
        default="300",
        description=("How often the background prober checks integration health"),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=30,
        max_value=3600,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_http_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "HTTP timeout for OAuth token exchange across device,"
            " authorization-code, and client-credentials flows"
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=5.0,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_device_flow_max_wait_seconds",
        type=SettingType.INTEGER,
        default="600",
        description=(
            "Maximum time to poll the OAuth device-flow token endpoint before giving up"
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=60,
        max_value=7200,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="rate_limit_coordinator_poll_timeout_seconds",
        type=SettingType.FLOAT,
        default="0.5",
        description=("Poll timeout for the shared-state rate-limit coordinator"),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=10.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="webhook_receipt_retention_days",
        type=SettingType.INTEGER,
        default="90",
        description=(
            "Default retention window for webhook receipts (days)."
            " 0 disables the sweep entirely; per-connection overrides on"
            " the connection itself take precedence when set."
        ),
        group="Webhooks",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=36_500,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="webhook_receipt_cleanup_tick_seconds",
        type=SettingType.FLOAT,
        default="86400.0",
        description=(
            "Wall-clock interval between webhook-receipt sweep ticks."
            " Receipts are retained in days; a daily sweep is the right"
            " granularity. Operators tune the *window*"
            " (``integrations.webhooks.receipt_retention_days`` or the"
            " per-connection override) rather than the *cadence*."
            " Default 24h. Resolved per-tick by"
            " ``_resolve_webhook_receipt_cleanup_tick_seconds``, so"
            " operator changes take effect on the next tick without"
            " restart."
        ),
        group="Webhooks",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=604800.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_device_flow_poll_interval_seconds",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "Initial polling interval for the OAuth device-code flow."
            " The IETF spec lets the server widen this dynamically with"
            " a slow_down response (the dynamic widening is preserved);"
            " this setting controls only the starting cadence."
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=60,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_idempotency_retention_seconds",
        type=SettingType.FLOAT,
        default="600.0",
        description=(
            "Retention window (seconds) for consumed OAuth state rows."
            " Consumed rows older than this are reaped by the periodic"
            " cleanup task. Sized to absorb realistic IdP redelivery"
            " envelopes (provider retries, browser back/forward, CDN"
            " replays) without growing the table indefinitely."
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=86_400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="github_api_url",
        type=SettingType.STRING,
        default="https://api.github.com",
        description=(
            "GitHub API base URL.  Override for GitHub Enterprise"
            " installations (e.g. ``https://github.example.com/api/v3``)"
            " or self-hosted GitHub-compatible services."
        ),
        group="GitHub",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^https?://[\w.\-:]+(?:/.*)?$",
    )
)
