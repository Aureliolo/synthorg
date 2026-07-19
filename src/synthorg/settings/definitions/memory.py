"""Memory namespace setting definitions (fine-tune group in memory_fine_tune)."""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="backend",
        type=SettingType.ENUM,
        default="sqlvector",
        description=(
            "Memory backend implementation. 'sqlvector' is durable and"
            " semantically searchable. 'inmemory' is DISCOURAGED: it"
            " matches by substring and loses every memory on restart."
        ),
        group="General",
        enum_values=("sqlvector", "composite", "inmemory"),
        restart_required=True,
    )
)

_r.register(
    # lint-allow: restart-required -- baked into the frozen CompanyMemoryConfig
    # at startup; a change applies on the next process start.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="default_level",
        type=SettingType.ENUM,
        default="persistent",
        description=(
            "Default memory persistence level for agents. Baked into the"
            " company memory config at startup, so a change applies on the"
            " next restart."
        ),
        group="General",
        enum_values=("none", "session", "project", "persistent"),
        restart_required=True,
    )
)

_r.register(
    # lint-allow: restart-required -- baked into the frozen ConsolidationConfig
    # at startup; a change applies on the next process start.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="consolidation_interval",
        type=SettingType.ENUM,
        default="daily",
        description=(
            "How often to consolidate and archive memories. Baked into the"
            " consolidation config at startup, so a change applies on the"
            " next restart."
        ),
        group="Maintenance",
        level=SettingLevel.ADVANCED,
        enum_values=("hourly", "daily", "weekly", "never"),
        restart_required=True,
    )
)

# ── Embedding overrides (advanced) ───────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="embedder_provider",
        type=SettingType.STRING,
        default=None,
        description="Override embedding provider (advanced)",
        group="Embedding",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="embedder_model",
        type=SettingType.STRING,
        default=None,
        description="Override embedding model (advanced)",
        group="Embedding",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="embedder_dims",
        type=SettingType.INTEGER,
        default=None,
        description="Override embedding vector dimensions (advanced)",
        group="Embedding",
        level=SettingLevel.ADVANCED,
        min_value=1,
    )
)

# ── Consolidation batch size ─────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="consolidation_enforce_batch_size",
        type=SettingType.INTEGER,
        default="1000",
        description=(
            "Number of memory records evicted per batch when enforcing"
            " the max-memories cap during consolidation"
        ),
        group="Maintenance",
        level=SettingLevel.ADVANCED,
        min_value=100,
        max_value=10_000,
    )
)

# ── Kill switches (CFG-1 audit) ──────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="consolidation_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master kill switch for memory consolidation. When False"
            " the consolidation scheduler stays constructed but every"
            " tick short-circuits -- safe way to pause consolidation"
            " without tearing down lifecycle plumbing."
        ),
        group="Maintenance",
        level=SettingLevel.ADVANCED,
    )
)
