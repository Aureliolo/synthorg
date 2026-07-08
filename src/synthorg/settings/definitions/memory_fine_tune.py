"""Memory namespace setting definitions: embedding fine-tune group."""

from typing import Final

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

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
        key="fine_tune_query_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model used to synthesise natural retrieval queries"
            " during embedding fine-tune data generation, selected through the"
            " model picker (a `{provider, model_id}` reference). Empty (default)"
            " uses the dependency-free extractive generator, so no LLM cost is"
            " incurred unless an operator opts in; an empty ref provider selects"
            " the first registered provider."
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

FINE_TUNE_MAX_TASKS_PER_STATUS: Final[int] = 1000
"""Per-status cap on how many tasks a single trajectory harvest scans."""

FINE_TUNE_PER_AGENT_MEMORY_LIMIT: Final[int] = 1000
"""Per-agent cap on how many memory records a trajectory harvest scans."""

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

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_max_tasks_per_status",
        type=SettingType.INTEGER,
        default=str(FINE_TUNE_MAX_TASKS_PER_STATUS),
        description=(
            "Per-status cap on how many tasks a single trajectory"
            " harvest scans when assembling embedding fine-tune training"
            " pairs. Bounds the working-history scan per task status."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100_000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_per_agent_memory_limit",
        type=SettingType.INTEGER,
        default=str(FINE_TUNE_PER_AGENT_MEMORY_LIMIT),
        description=(
            "Per-agent cap on how many memory records a single trajectory"
            " harvest scans when assembling embedding fine-tune training"
            " pairs."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100_000,
    )
)

_r.register(
    # lint-allow: restart-required -- resolved once at boot into the
    # fine-tune image cache; the CLI changes the image only by recreating
    # the backend container, so a mid-run DB write would silently drift
    # from the value ephemeral stage containers actually use.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_image",
        type=SettingType.STRING,
        default="",
        description=(
            "Container image for ephemeral fine-tune stage containers."
            " Resolution precedence at backend startup: DB override >"
            " SYNTHORG_FINE_TUNE_IMAGE env var (injected by the CLI when"
            " fine_tuning=true) > registered code default. Empty means no"
            " image is configured and fine-tune runs execute in-process"
            " (bare-metal installs with the torch extras)."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_FINE_TUNE_IMAGE",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_default_gpu",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Request GPU passthrough on ephemeral fine-tune stage"
            " containers when the run does not specify an execution"
            " config. Resolved per run start, so a change applies"
            " without a restart."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_stage_timeout_seconds",
        type=SettingType.FLOAT,
        default="7200.0",
        description=(
            "Maximum wall-clock time for a single fine-tune pipeline"
            " stage (both in-process and containerised). Resolved per"
            " run start, so a change applies without a restart."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=86_400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_memory_limit",
        type=SettingType.STRING,
        default="8g",
        description=(
            "Memory limit for ephemeral fine-tune stage containers, as a"
            " Docker size string ('512b', '64k', '64m', '8G'; leading"
            " digit non-zero). Resolved per run start, so a change"
            " applies without a restart."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        validator_pattern=r"^[1-9]\d*[bkmgBKMG]?$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="fine_tune_data_volume",
        type=SettingType.STRING,
        default="synthorg-data",
        description=(
            "Named Docker volume mounted rw at /data inside ephemeral"
            " fine-tune stage containers (training data in, checkpoints"
            " out). Matches the compose data volume in CLI installs."
            " Must be a Docker volume NAME: a path here would become a"
            " host bind-mount, so the pattern rejects '/', '\\' and ':'."
            " Resolved per run start, so a change applies without a"
            " restart."
        ),
        group="Fine-Tune",
        level=SettingLevel.ADVANCED,
        env_var_override="SYNTHORG_FINE_TUNE_DATA_VOLUME",
        validator_pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}$",
    )
)
