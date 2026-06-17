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
        key="model_refresh_mode",
        type=SettingType.ENUM,
        default="off",
        # Mirrors synthorg.providers.management.refresh_config.RefreshMode;
        # kept literal here to avoid a definitions -> providers import cycle
        # (parity asserted by test_model_refresh_settings).
        enum_values=("off", "manual_only", "detect_only", "reconcile_recommend"),
        description=(
            "Periodic model-refresh/reconcile mode. 'off' (the safe default)"
            " schedules nothing; 'manual_only' runs only on the explicit"
            " refresh endpoint; 'detect_only' periodically probes and flags"
            " removed models stale; 'reconcile_recommend' also persists"
            " refreshed metadata and feeds upgrade recommendations. Re-read"
            " live each cycle, so changes apply without a restart."
        ),
        group="Model Refresh",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="model_refresh_interval_seconds",
        type=SettingType.FLOAT,
        default="86400.0",
        description=(
            "Cadence in seconds between automatic model-refresh cycles when"
            " the mode schedules a loop. Floored at the scheduler minimum."
        ),
        group="Model Refresh",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=604800.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.PROVIDERS,
        key="model_refresh_auto_apply_within_family",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "When true, strictly in-family upgrade recommendations (same"
            " family, newer generation, no capability regression) are"
            " auto-applied by reassigning pinned agents instead of being"
            " parked for human approval. Cross-family upgrades always wait"
            " for approval. Off by default."
        ),
        group="Model Refresh",
        level=SettingLevel.ADVANCED,
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
