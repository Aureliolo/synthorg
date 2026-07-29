"""Memory namespace setting definitions (fine-tune group in memory_fine_tune)."""

from synthorg.core.vector_limits import (
    HNSW_HALFVEC_MAX_DIMENSIONS,
    HNSW_VECTOR_MAX_DIMENSIONS,
    STORAGE_MAX_DIMENSIONS,
)
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

# ── Embedding ────────────────────────────────────────────────────
#
# Both are read once, by the boot-time backend wiring, to build the
# embedder and size the dense index. Changing the model mid-process
# would leave every stored vector at an incomparable width, so these are
# deliberately restart-scoped rather than hot-reloadable.
#
# The model is a MODEL_REF rather than a bare string so the type itself
# refuses a provider-less value at write time. A provider derived from a
# model name is what the Explicit Provider Binding rule forbids, and it
# produced bindings naming providers no registry had.

_r.register(
    # lint-allow: restart-required -- read once when the boot path builds
    # the embedder; a mid-process change would orphan every stored vector.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="embedder_model",
        type=SettingType.MODEL_REF,
        default=None,
        description=(
            "Embedding model agents recall through. Nothing is chosen for"
            " you: unset means memory stays off. Choose the builtin/hashing"
            " pair to run without an embedding model, which matches shared"
            " vocabulary rather than meaning and gives agents materially"
            " weaker recall. Applies on the next restart."
        ),
        group="Embedding",
        level=SettingLevel.BASIC,
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
            "Pin the embedding vector width instead of measuring it from"
            " the model (advanced). Applies on the next restart. At or"
            f" below {HNSW_VECTOR_MAX_DIMENSIONS} the dense index is exact;"
            f" up to {HNSW_HALFVEC_MAX_DIMENSIONS} it is built at half"
            " precision; above that no approximate index can be built and"
            " every dense search reads the whole corpus. Pinning a width"
            " below what the model emits truncates each vector, which is"
            " only sound for a Matryoshka-capable model."
        ),
        group="Embedding",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=STORAGE_MAX_DIMENSIONS,
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
        key="retro_capture_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether a completed objective's lead distils a retrospective into"
            " org and agent memory. On means finished work feeds the standing"
            " organisation, so a later objective builds on it; off leaves the"
            " loop open. Re-read per objective, so a change applies immediately."
        ),
        group="Learning",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="retro_session_max_turns",
        type=SettingType.INTEGER,
        default="8",
        description=(
            "Hard turn cap for the SHIP-time retrospective session the lead"
            " runs. Higher lets the lead recall and self-review more before"
            " submitting, at more cost per objective."
        ),
        group="Learning",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="retro_session_cost_ceiling",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "Per-session spend ceiling (base currency) for the retrospective"
            " session; it halts once accumulated cost reaches this."
        ),
        group="Learning",
        level=SettingLevel.ADVANCED,
        min_value=0.01,
        max_value=100.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="retro_session_timeout_seconds",
        type=SettingType.FLOAT,
        default="180.0",
        description=(
            "Wall-clock ceiling for one retrospective capture. A backstop to the"
            " session's own cost and turn caps so a hung distillation cannot"
            " occupy a background slot indefinitely."
        ),
        group="Learning",
        level=SettingLevel.ADVANCED,
        min_value=10.0,
        max_value=1800.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="planning_memory_recall_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether the owner-run planning session recalls org playbooks, past"
            " retros, and prior-initiative memory when decomposing an objective."
            " On means plans build on what the organisation already learned; off"
            " plans from priors only. Applied when the coordinator is (re)built."
        ),
        group="Learning",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="planning_memory_digest_budget",
        type=SettingType.INTEGER,
        default="1000",
        description=(
            "Token cap for the org/retro memory digest pre-seeded into the"
            " planning brief. 0 injects no digest (the owner can still recall"
            " with the search_memory tool). Applied when the coordinator is"
            " (re)built."
        ),
        group="Learning",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=8000,
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
