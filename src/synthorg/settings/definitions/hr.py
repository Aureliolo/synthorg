"""HR namespace setting definitions.

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

# ── Closed-loop evaluation cycle scheduler ───────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_cycle_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Master switch for the periodic evaluation-loop cycle"
            " scheduler. Off by default: the scheduler is always constructed"
            " and started, but every tick short-circuits until an operator"
            " opts in (a cycle evaluates every agent's five-pillar"
            " performance and can route corrective actions to training)."
            " Re-read live per tick, so toggling it takes effect on the next"
            " tick with no restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_cycle_paused",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Pause flag for the evaluation-loop cycle scheduler. When True"
            " the periodic scheduler stays resident but every tick"
            " short-circuits, pausing evaluation cycles without a restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_cycle_interval_seconds",
        type=SettingType.FLOAT,
        default="86400.0",
        description=(
            "Cadence between automatic evaluation-loop cycles. Default 24h"
            " keeps the closed loop low-overhead; values below 60 seconds are"
            " rejected at write time (registry minimum). Re-read live per tick,"
            " so a change applies on the next sleep with no restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        restart_required=False,
        min_value=60.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_cycle_window_hours",
        type=SettingType.FLOAT,
        default="168.0",
        description=(
            "Look-back window each evaluation cycle collects agent"
            " performance metrics over. Default 168h (7 days) smooths"
            " single-task noise into a stable pillar signal. Re-read live each"
            " cycle, so a change applies on the next cycle with no restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        restart_required=False,
        min_value=1.0,
    )
)

# ── Eval-loop IDENTIFY / PROPOSE strategy selection ──────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_pattern_identifier_mode",
        type=SettingType.ENUM,
        default="deterministic",
        enum_values=("deterministic", "llm"),
        description=(
            "Strategy that identifies cross-agent weakness patterns each"
            " cycle. 'deterministic' counts agents scoring below the pillar"
            " thresholds (no provider). 'llm' weighs the pillar scores with a"
            " dedicated model call and degrades to deterministic when no model"
            " or provider is available. EvalLoopSettingsSubscriber rebuilds and"
            " swaps the strategy on a change, so it applies without a restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_fix_proposer_mode",
        type=SettingType.ENUM,
        default="deterministic",
        enum_values=("deterministic", "llm"),
        description=(
            "Strategy that proposes remediation actions for identified"
            " patterns. 'deterministic' maps each pillar via the static action"
            " table. 'llm' proposes actions with a dedicated model call and"
            " degrades to the table when no model or provider is available."
            " EvalLoopSettingsSubscriber rebuilds and swaps the strategy on a"
            " change, so it applies without a restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_llm_model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier for the eval-loop 'llm' identify/propose"
            " strategies. Empty (the default) keeps both steps deterministic"
            " regardless of their mode. EvalLoopSettingsSubscriber rebuilds the"
            " strategies on a change, so it applies without a restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_llm_provider",
        type=SettingType.STRING,
        default="",
        description=(
            "Provider name resolving the eval-loop 'llm' strategy model."
            " Empty selects the first registered provider."
            " EvalLoopSettingsSubscriber rebuilds the strategies on a change,"
            " so it applies without a restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

# ── Dynamic-scaling kill switch ──────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="scaling_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Master switch for the dynamic auto-scaling pipeline. Off by"
            " default: a scaling evaluation produces real hire and prune"
            " decisions from workload / budget / skill / performance signals"
            " (routed through the approval gate). The ScalingService and the"
            " durable HiringService are ghost-wired at boot, and the switch is"
            " enforced live at the evaluate endpoint, so toggling it takes"
            " effect on the next request with no restart."
        ),
        group="Scaling",
        level=SettingLevel.ADVANCED,
        restart_required=False,
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
        key="scaling_workload_max_concurrent_tasks",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Max concurrent tasks per agent used as the utilisation"
            " denominator when the workload scaler reads agent load."
        ),
        group="Scaling",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
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
        key="performance_quality_ci_weight",
        type=SettingType.FLOAT,
        default="0.4",
        description=(
            "Weight of the CI signal in the composite quality score."
            " The LLM-judge weight is its complement (1 - this value)."
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
