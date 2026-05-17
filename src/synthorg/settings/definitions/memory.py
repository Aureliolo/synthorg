"""Memory namespace setting definitions."""

from typing import Final

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="backend",
        type=SettingType.STRING,
        default="mem0",
        description="Memory backend implementation",
        group="General",
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="default_level",
        type=SettingType.ENUM,
        default="persistent",
        description="Default memory persistence level for agents",
        group="General",
        enum_values=("none", "session", "project", "persistent"),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="consolidation_interval",
        type=SettingType.ENUM,
        default="daily",
        description="How often to consolidate and archive memories",
        group="Maintenance",
        level=SettingLevel.ADVANCED,
        enum_values=("hourly", "daily", "weekly", "never"),
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

# ── Fine-tune VRAM-to-batch-size mapping ────────────────────────
# Operator-tunable preflight table used by the memory controller
# to size embedding fine-tune batches. Each tuple is
# ``(min_vram_gb, batch_size)``; the largest matching threshold
# wins. Fallback module constant in api/controllers/memory.py
# mirrors the default so the preflight check still produces a
# sensible suggestion when the resolver is unavailable.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_vram_batch_table",
        type=SettingType.JSON,
        default="[[40.0, 128], [16.0, 64], [8.0, 32]]",
        description=(
            "VRAM-to-batch-size table for embedding fine-tune"
            " preflight. JSON array of ``[min_vram_gb, batch_size]``"
            " pairs sorted descending by VRAM threshold. Operators"
            " add rows for new GPU profiles."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_chunk_size",
        type=SettingType.INTEGER,
        default="512",
        description=(
            "Word-boundary chunk size for synthetic data generation"
            " during embedding fine-tune. Chunks of exactly this many"
            " words are produced (last chunk may be shorter)."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=64,
        max_value=4096,
    )
)

# ── Fine-tune preflight thresholds ──────────────────────────────
# Module-level defaults exported as ``Final[int]`` so the memory
# controller imports them without re-introducing bare numeric
# literals in business logic.  ``definitions/`` is allowlisted by
# the no-magic-numbers gate, which is the canonical home for every
# numeric tuning knob in the codebase.

FINE_TUNE_DEFAULT_BATCH_SIZE: Final[int] = 16
"""Fallback batch size used when no VRAM tier matches (CPU-only / sub-threshold)."""

FINE_TUNE_MIN_DOCS_REQUIRED: Final[int] = 10
"""Hard floor on source-corpus document count for embedding fine-tune."""

FINE_TUNE_MIN_DOCS_RECOMMENDED: Final[int] = 50
"""Soft minimum: corpora below this size emit a preflight warn band."""

FINE_TUNE_PREFLIGHT_MAX_DEPTH: Final[int] = 8
"""Max directory recursion depth for the preflight document scan.

Bounds the ``_check_documents`` walk so a pathologically deep
(symlink-loop / generated) source tree cannot turn the preflight
endpoint into an unbounded filesystem traversal."""

FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S: Final[float] = 5.0
"""Wall-clock deadline (seconds) for the preflight document scan.

Independent of the depth cap: a wide but shallow tree on a slow /
stale-handle NFS mount is bounded by time even when depth is fine.
On either bound the check returns a ``warn`` band, never a hang."""

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_default_batch_size",
        type=SettingType.INTEGER,
        default=str(FINE_TUNE_DEFAULT_BATCH_SIZE),
        description=(
            "Fallback batch size for embedding fine-tune when the VRAM"
            " tier table does not produce a match (CPU-only or"
            " sub-threshold GPU)."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=1024,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_min_docs_required",
        type=SettingType.INTEGER,
        default=str(FINE_TUNE_MIN_DOCS_REQUIRED),
        description=(
            "Hard floor on source-corpus document count for embedding"
            " fine-tune. Preflight rejects corpora below this size."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=1_000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_min_docs_recommended",
        type=SettingType.INTEGER,
        default=str(FINE_TUNE_MIN_DOCS_RECOMMENDED),
        description=(
            "Soft minimum on source-corpus document count for embedding"
            " fine-tune. Preflight emits a warn band for corpora at or"
            " below this size."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10_000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_preflight_max_depth",
        type=SettingType.INTEGER,
        default=str(FINE_TUNE_PREFLIGHT_MAX_DEPTH),
        description=(
            "Max directory recursion depth for the preflight document"
            " scan. Bounds the walk so a pathologically deep source"
            " tree cannot make the preflight endpoint traverse the"
            " filesystem unbounded; exceeding it returns a warn band."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=64,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_preflight_walk_timeout_s",
        type=SettingType.FLOAT,
        default=str(FINE_TUNE_PREFLIGHT_WALK_TIMEOUT_S),
        description=(
            "Wall-clock deadline (seconds) for the preflight document"
            " scan. A wide but shallow tree on a slow / stale-handle"
            " mount is bounded by time even when depth is fine;"
            " exceeding it returns a warn band rather than hanging."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
    )
)
