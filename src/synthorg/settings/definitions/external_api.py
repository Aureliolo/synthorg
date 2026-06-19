"""External-API namespace setting definitions.

Governs the first-class external-access tool: its master feature flag /
provider discriminator and the default per-call limits applied when a
connection does not carry its own override.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.EXTERNAL_API,
        key="enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Master switch for the governed external-access tool. Off by"
            " default (safe egress posture): the tool is not registered, so"
            " agents cannot make external API calls until an operator opts"
            " in. When false the tool is not registered."
        ),
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.EXTERNAL_API,
        key="provider_type",
        type=SettingType.STRING,
        default="httpx",
        description=(
            "Discriminator selecting the ExternalAccessProvider strategy"
            " used for egress. 'httpx' (default) makes DNS-pinned"
            " requests directly; future strategies (e.g. a sidecar proxy)"
            " register under their own key."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^[a-z][a-z0-9_]*$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.EXTERNAL_API,
        key="default_max_response_bytes",
        type=SettingType.INTEGER,
        default="1048576",
        description=(
            "Hard cap on the response body size (bytes) read from an"
            " external API before truncation, bounding agent memory."
        ),
        group="Limits",
        level=SettingLevel.ADVANCED,
        min_value=1024,
        max_value=10485760,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.EXTERNAL_API,
        key="default_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Maximum wall-clock time an external API request may run"
            " before it is cancelled."
        ),
        group="Limits",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.EXTERNAL_API,
        key="default_max_rpm",
        type=SettingType.INTEGER,
        default="60",
        description=(
            "Default per-connection rate limit (requests per minute)"
            " applied when a connection carries no rate_limiter override."
        ),
        group="Limits",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10000,
    )
)
