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
        yaml_path="integrations.health.probe_interval_seconds",
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
        yaml_path="integrations.oauth.http_timeout_seconds",
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
        yaml_path="integrations.oauth.device_flow_max_wait_seconds",
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
        yaml_path="integrations.rate_limit.coordinator_poll_timeout_seconds",
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
        yaml_path="integrations.webhooks.receipt_retention_days",
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
        yaml_path="integrations.oauth_device_flow_poll_interval_seconds",
    )
)
