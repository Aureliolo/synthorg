"""Providers namespace setting definitions."""

from synthorg.providers.routing.strategies import STRATEGY_MAP
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="routing_strategy",
        type=SettingType.ENUM,
        default="cost_aware",
        description="Model routing strategy",
        group="Routing",
        enum_values=tuple(sorted(STRATEGY_MAP.keys())),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="retry_max_attempts",
        type=SettingType.INTEGER,
        default="3",
        description="Maximum retry attempts for transient provider errors",
        group="Resilience",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="configs",
        type=SettingType.JSON,
        default=None,
        description="LLM provider configurations (JSON object keyed by name)",
        group="General",
        sensitive=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="discovery_allowlist",
        type=SettingType.JSON,
        default=None,
        description=(
            "Trusted host:port pairs for provider discovery SSRF bypass (JSON)"
        ),
        group="Discovery",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="ollama_default_port",
        type=SettingType.INTEGER,
        default="11434",
        description=(
            "Default port used to detect a self-hosted Ollama provider when"
            " its ``litellm_provider`` field is not set explicitly."
            " The health prober treats a base URL bound to this port as"
            " an Ollama endpoint and pings the root URL (which returns a"
            " liveness string) instead of ``/models``."
        ),
        group="Ollama",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=65535,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="cassette_mode",
        type=SettingType.ENUM,
        default="off",
        enum_values=("off", "record", "replay"),
        description=(
            "Deterministic recorded-LLM cassette mode. 'off' is inert;"
            " 'record' wraps every provider, delegates to the real driver"
            " and persists each response keyed by request; 'replay' serves"
            " recorded responses with zero real LLM calls and never"
            " constructs a real driver. Baked in at process startup."
        ),
        group="Cassette",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="cassette_path",
        type=SettingType.STRING,
        default=None,
        description=(
            "Filesystem path to the cassette document. Required whenever"
            " providers.cassette_mode is not 'off'. Baked in at process"
            " startup."
        ),
        group="Cassette",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)
