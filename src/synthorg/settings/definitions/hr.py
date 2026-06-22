"""HR namespace setting definitions (CFG-1 audit).

Covers kill switches and tuning knobs for the HR subsystems:
training pipeline, evaluation metrics, personality composite weights.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

# ── Training pipeline kill switch ────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="training_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master kill switch for the training pipeline. When False,"
            " training ingestion and curation are paused."
        ),
        group="Training",
        level=SettingLevel.ADVANCED,
    )
)

# ── Promotion cycle kill switch ──────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="promotion_cycle_paused",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Pause flag for the automatic promotion cycle. When True the"
            " periodic scheduler stays resident but every tick"
            " short-circuits, so the org stops re-evaluating agent"
            " seniority without a restart."
        ),
        group="Promotion",
        level=SettingLevel.ADVANCED,
    )
)

# ── Evaluation metric granular toggles ───────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="promotion_cycle_interval_seconds",
        type=SettingType.FLOAT,
        default="3600.0",
        description=(
            "Cadence between automatic promotion-cycle scans. The"
            " scheduler floors this at 60 seconds."
        ),
        group="Promotion",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="promotion_cooldown_hours",
        type=SettingType.INTEGER,
        default="24",
        description="Hours between consecutive promotions/demotions for an agent.",
        group="Promotion",
        level=SettingLevel.ADVANCED,
        min_value=0,
    )
)

# ── Workload scaling tuning knobs ────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="scaling_hire_threshold",
        type=SettingType.FLOAT,
        default="0.85",
        description="Utilisation fraction above which the scaler recommends hiring.",
        group="Scaling",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="scaling_prune_threshold",
        type=SettingType.FLOAT,
        default="0.30",
        description="Utilisation fraction below which the scaler recommends pruning.",
        group="Scaling",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="scaling_cooldown_seconds",
        type=SettingType.INTEGER,
        default="3600",
        description="Cooldown between same-type scaling actions (hire/prune).",
        group="Scaling",
        level=SettingLevel.ADVANCED,
        min_value=0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="scaling_max_hires_per_day",
        type=SettingType.INTEGER,
        default="3",
        description="Daily cap on scaler-driven hires.",
        group="Scaling",
        level=SettingLevel.ADVANCED,
        min_value=0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="scaling_max_prunes_per_day",
        type=SettingType.INTEGER,
        default="1",
        description="Daily cap on scaler-driven prunes.",
        group="Scaling",
        level=SettingLevel.ADVANCED,
        min_value=0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="scaling_trigger_interval_seconds",
        type=SettingType.INTEGER,
        default="900",
        description="Polling cadence of the batched scaling trigger loop.",
        group="Scaling",
        level=SettingLevel.ADVANCED,
        min_value=60,
    )
)

# ── Performance calibration sampling ─────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="performance_llm_sampling_rate",
        type=SettingType.FLOAT,
        default="0.01",
        description=(
            "Fraction of agent collaboration events sampled by the LLM"
            " calibrator for quality evaluation (0.01 = 1%)."
        ),
        group="Performance",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="evaluation_quality_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Record quality metrics during evaluation",
        group="Evaluation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="evaluation_cost_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Record cost metrics during evaluation",
        group="Evaluation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="evaluation_latency_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Record latency metrics during evaluation",
        group="Evaluation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="evaluation_task_count_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Record task-count metrics during evaluation",
        group="Evaluation",
        level=SettingLevel.ADVANCED,
    )
)
