"""A2A (agent-to-agent federation) namespace setting definitions.

Covers the federation HTTP client and push-notification signature
verification.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.A2A,
        key="client_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "HTTP timeout for the A2A federation client and gateway-side peer calls"
        ),
        group="Client",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=5.0,
        max_value=300.0,
        yaml_path="a2a.client_timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.A2A,
        key="push_verification_clock_skew_seconds",
        type=SettingType.INTEGER,
        default="300",
        description=(
            "Maximum accepted clock skew when verifying HMAC-signed"
            " A2A push notifications"
        ),
        group="Push Verification",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=3600,
        yaml_path="a2a.push_verification_clock_skew_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.A2A,
        key="max_message_parts",
        type=SettingType.INTEGER,
        default="100",
        description=(
            "Maximum number of message parts accepted in a single"
            " A2A message/send JSON-RPC request. Resolved per request"
            " so a runtime change applies on the next inbound message."
        ),
        group="Gateway",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10000,
        yaml_path="a2a.max_message_parts",
    )
)
