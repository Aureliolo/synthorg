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
            " semantically searchable. 'inmemory' is DISCOURAGED: it ranks"
            " by shared terms rather than by meaning, and loses every"
            " memory on restart. 'composite' routes namespaces across"
            " several wired backends, aggregating their capabilities."
        ),
        group="General",
        enum_values=("sqlvector", "composite", "inmemory"),
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
#
# All three are read once, by the boot-time backend wiring, to build the
# embedder and size the dense index. Changing the model mid-process
# would leave every stored vector at an incomparable width, so these are
# deliberately restart-scoped rather than hot-reloadable.

_r.register(
    # lint-allow: restart-required -- read once when the boot path builds
    # the embedder; a mid-process change would orphan every stored vector.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="embedder_provider",
        type=SettingType.STRING,
        default=None,
        description=(
            "Override embedding provider (advanced). Applies on the next restart."
        ),
        group="Embedding",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

_r.register(
    # lint-allow: restart-required -- read once when the boot path builds
    # the embedder; a mid-process change would orphan every stored vector.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="embedder_model",
        type=SettingType.STRING,
        default=None,
        description=(
            "Override embedding model (advanced). Applies on the next restart."
        ),
        group="Embedding",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

_r.register(
    # lint-allow: restart-required -- sizes the dense index at boot; the
    # index is keyed by width, so a change re-indexes on the next start.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="embedder_dims",
        type=SettingType.INTEGER,
        default=None,
        description=(
            "Override embedding vector dimensions (advanced). Applies on"
            " the next restart."
        ),
        group="Embedding",
        level=SettingLevel.ADVANCED,
        min_value=1,
        restart_required=True,
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

# ── Kill switches ──────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="distillation_capture_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether a finished task's trajectory is distilled into"
            " durable memory. Off means agents keep recalling but stop"
            " learning: a later run of the same objective starts from"
            " nothing. Re-read per task, so a change applies immediately."
        ),
        group="Learning",
    )
)

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
