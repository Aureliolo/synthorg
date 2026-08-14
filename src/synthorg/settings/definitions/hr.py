# module-kind: declarative
"""HR namespace setting definitions.

Covers kill switches and tuning knobs for the HR subsystems:
training pipeline, evaluation metrics, personality composite weights,
and the model a new hire is registered on.
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
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="eval_loop_llm_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for the eval-loop 'llm' identify/propose"
            " strategies, selected through the model picker (a `{provider,"
            " model_id}` reference). Empty (the default) keeps both steps"
            " deterministic regardless of their mode; a ref whose provider is"
            " blank keeps that step deterministic (no provider is resolved)."
            " EvalLoopSettingsSubscriber rebuilds the strategies on a change,"
            " so it applies without a restart."
        ),
        group="Evaluation",
        level=SettingLevel.ADVANCED,
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
    )
)

# ── New-hire model binding ───────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="new_hire_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model a newly hired agent is bound to when an"
            " approved hire is instantiated. A model reference (`{provider,"
            " model_id}`) because a provider is a registered connection with"
            " its own credentials and endpoint, so a bare model id names no"
            " dispatch target. Unset refuses the hire naming this setting"
            " rather than registering an agent that joins the roster looking"
            " staffed and fails every dispatch; the operator can re-bind any"
            " individual agent afterwards from its detail page. Re-read live"
            " per instantiation, so binding it arms the next approval with no"
            " restart."
        ),
        # Every hire reads it, not only a scaler-proposed one: an operator's
        # manual hire and the staffing sweep's approval-gated one bind through
        # the same instantiation. Grouped under Scaling it reads as a knob an
        # org with auto-scaling off can leave unset, and that org's first hire
        # then refuses.
        group="Hiring",
        level=SettingLevel.ADVANCED,
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

# ── Department health derivation ─────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="department_health_window_days",
        type=SettingType.INTEGER,
        default="7",
        description=(
            "Rolling window (days) of terminal task runs used to derive a"
            " department's dashboard health from real outcomes. Re-read per"
            " health request, so a change applies with no restart."
        ),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=365,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="department_health_min_runs",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Minimum terminal task runs in the window before a department"
            " health score is shown. Below this the dashboard shows an"
            " explicit no-data state instead of a misleading number, so"
            " zero-activity departments never read as fully healthy."
        ),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=1000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="training_curation_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the `llm_curated` training-curation strategy"
            " runs on. A model reference (`{provider, model_id}`) because a"
            " provider is a registered connection with its own credentials and"
            " endpoint, so a bare model id names no dispatch target. Unset"
            " degrades curation to deterministic relevance scoring rather than"
            " deciding what a new hire learns on a connection nobody chose."
        ),
        group="Training",
        level=SettingLevel.ADVANCED,
    )
)
