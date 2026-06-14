# module-kind: declarative
"""Engine namespace setting definitions."""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="personality_trimming_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Enable token-based personality trimming when section exceeds budget"
        ),
        group="Personality Trimming",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="personality_max_tokens_override",
        type=SettingType.INTEGER,
        default="0",
        description=(
            "Global override for personality section token limit "
            "(0 = use profile defaults per tier: large=500, medium=200, small=80)"
        ),
        group="Personality Trimming",
        min_value=0,
        max_value=10000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="personality_trimming_notify",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Publish a WebSocket notification on the agents channel "
            "when personality trimming activates for an agent"
        ),
        group="Personality Trimming",
    )
)

# ── Approval gate ────────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="approval_interrupt_timeout_seconds",
        type=SettingType.FLOAT,
        default="300.0",
        description=(
            "How long an approval gate waits for a human decision before"
            " the task is interrupted"
        ),
        group="Approval Gate",
        level=SettingLevel.ADVANCED,
        min_value=30.0,
        max_value=3600.0,
    )
)

# ── Health judge ────────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="max_subworkflow_depth",
        type=SettingType.INTEGER,
        default="16",
        description=(
            "Maximum runtime nesting depth for subworkflow calls. The"
            " ``WorkflowExecutionService`` rejects activation past this"
            " limit to bound per-workflow stack depth and protect against"
            " infinite recursion through cyclic subworkflow references."
        ),
        group="Workflow Execution",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=64,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="health_quality_degradation_threshold",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Number of consecutive INCORRECT steps before the health judge"
            " escalates a quality-degradation signal"
        ),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10,
    )
)

# ── Kill switches ────────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="evolution_enabled",
        type=SettingType.BOOLEAN,
        # Default "false" matches ``SelfImprovementConfig.enabled``
        # (False) so the resolver-up and resolver-down paths produce
        # the same boolean -- a mismatch would let the kill-switch
        # flip behaviour during a settings outage.  The feature is
        # opt-in by design (research/experimental); operators turn
        # it on per-deployment via YAML or the /settings UI.
        default="false",
        description=(
            "Master kill switch for the agent evolution system."
            " When False (default), evolution triggers never fire;"
            " set True to opt the deployment into evolution cycles."
        ),
        group="Evolution",
        level=SettingLevel.ADVANCED,
    )
)

# ── Quality + classification thresholds ─────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="classifier_rule_matched_confidence",
        type=SettingType.FLOAT,
        default="0.7",
        description=(
            "Confidence score assigned when a quality-classifier rule"
            " matches a step (used by RuleBasedStepClassifier)."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="classifier_fallback_confidence",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Confidence score assigned when a quality-classifier"
            " falls back to heuristic (no rule matched)."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="classification_detector_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Per-detector timeout in the classification pipeline."
            " Prevents a hung detector from blocking classification."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=600.0,
    )
)


_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="timeout_enforcement_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether asyncio.timeout wrappers on engine coroutines"
            " are enforced. Dev operators may disable for debugging;"
            " leave enabled in production."
        ),
        group="Safety",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="shutdown_tool_timeout_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description=(
            "Maximum time the graceful-shutdown strategy waits for an"
            " in-flight tool execution to complete before cancelling it."
        ),
        group="Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=600.0,
    )
)

# ── Routing scorer weights ──────────────────────────────────────
# Weights for the AgentTaskScorer score components. Sum is 1.1 with
# the tag bonus; capped at 1.0 by the caller. See
# docs/reference/scoring-hyperparameters.md for rationale.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="routing_weight_primary_skill",
        type=SettingType.FLOAT,
        default="0.4",
        description=(
            "Routing scorer: weight applied to the primary-skill"
            " overlap component. Drives how strongly an agent's"
            " primary-skill match steers task assignment."
        ),
        group="Task Routing",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="routing_weight_secondary_skill",
        type=SettingType.FLOAT,
        default="0.2",
        description=(
            "Routing scorer: weight applied to the secondary-skill"
            " overlap component (skills already counted as primary"
            " are excluded from this contribution)."
        ),
        group="Task Routing",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="routing_weight_tag_match_bonus",
        type=SettingType.FLOAT,
        default="0.1",
        description=(
            "Routing scorer: bonus added when every required tag is"
            " covered by the union of tags on matched skills."
        ),
        group="Task Routing",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="routing_weight_role_match_bonus",
        type=SettingType.FLOAT,
        default="0.2",
        description=(
            "Routing scorer: bonus added when the agent's role matches"
            " the subtask's required_role (case-insensitive)."
        ),
        group="Task Routing",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="routing_weight_seniority_alignment_bonus",
        type=SettingType.FLOAT,
        default="0.2",
        description=(
            "Routing scorer: bonus added when the agent's seniority"
            " level is within the complexity band the subtask declares."
        ),
        group="Task Routing",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="routing_min_score",
        type=SettingType.FLOAT,
        default="0.1",
        description=(
            "Minimum score threshold for a viable routing candidate."
            " Candidates below this score are filtered out before"
            " strategy ranking."
        ),
        group="Task Routing",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

# ── Model matcher score weights ─────────────────────────────────
# Three score components contribute up to ``matcher_tier_base_score`` /
# ``matcher_headroom_max_bonus`` / ``matcher_priority_max_bonus``;
# ``matcher_headroom_ratio_cap`` clamps the headroom curve;
# ``matcher_balanced_partial_credit`` is the balanced-priority bonus.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_tier_base_score",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Model matcher: floor score awarded when a model's tier"
            " satisfies the requirement before headroom and priority"
            " bonuses are applied."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_headroom_max_bonus",
        type=SettingType.FLOAT,
        default="0.25",
        description=(
            "Model matcher: maximum bonus when a model's context"
            " window comfortably exceeds the requirement (clamped"
            " by ``matcher_headroom_ratio_cap``)."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_priority_max_bonus",
        type=SettingType.FLOAT,
        default="0.25",
        description=(
            "Model matcher: maximum bonus from the priority-axis"
            " ranking within the matched tier (cost / quality / speed)."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_headroom_ratio_cap",
        type=SettingType.FLOAT,
        default="2.0",
        description=(
            "Model matcher: maximum context-headroom multiple credited."
            " Beyond this, additional context is wasted on the priority"
            " axis. Default 2.0 means a model with twice the requested"
            " context gets the full headroom bonus."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=100.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_balanced_partial_credit",
        type=SettingType.FLOAT,
        default="0.125",
        description=(
            "Model matcher: bonus awarded to balanced-priority"
            " requirements when no other ranking applies (i.e. the"
            " 'no strong preference' fallback)."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

# ── Heuristic grader thresholds ─────────────────────────────────
# Drives the rule-based ``HeuristicRubricGrader``. Pass-threshold is
# the probe-pass-ratio cutoff; pass/fail grades are the per-criterion
# scores assigned in each branch; confidence ceiling/bias derive the
# final confidence from the ratio.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="quality_heuristic_pass_threshold",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Heuristic grader: probe-pass-ratio cutoff for the"
            " PASS verdict. Below this, criteria receive the"
            " configured fail-grade."
        ),
        group="Quality Grader",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="quality_heuristic_pass_grade",
        type=SettingType.FLOAT,
        default="0.8",
        description=(
            "Heuristic grader: per-criterion grade when the probe"
            " pass-ratio is greater than or equal to"
            " ``quality_heuristic_pass_threshold``."
        ),
        group="Quality Grader",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="quality_heuristic_fail_grade",
        type=SettingType.FLOAT,
        default="0.3",
        description=(
            "Heuristic grader: per-criterion grade when the probe"
            " pass-ratio falls below ``quality_heuristic_pass_threshold``."
        ),
        group="Quality Grader",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="quality_heuristic_confidence_ceiling",
        type=SettingType.FLOAT,
        default="0.9",
        description=(
            "Heuristic grader: maximum confidence the heuristic"
            " strategy will report. Caps the value derived from the"
            " probe pass-ratio."
        ),
        group="Quality Grader",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="quality_heuristic_confidence_bias",
        type=SettingType.FLOAT,
        default="0.1",
        description=(
            "Heuristic grader: additive bias on the derived confidence."
            " Prevents a 0% pass-ratio from collapsing confidence to 0."
        ),
        group="Quality Grader",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

# ── Execution limits ────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="max_turns",
        type=SettingType.INTEGER,
        default="20",
        description=(
            "Hard cap on the number of LLM turns per agent execution."
            " Applied by AgentEngine.run when a caller does not pass an"
            " explicit max_turns; bounds runaway loops and per-task cost."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=1000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="task_engine_max_queue_size",
        type=SettingType.INTEGER,
        default="1000",
        description=(
            "Backpressure cap on the in-process task-mutation queue."
            " ``0`` means unbounded. Raise for high-throughput deployments"
            " with many concurrent agents; lower for resource-constrained"
            " hosts. Read once at TaskEngineConfig construction."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        min_value=0,
        max_value=1_000_000,
    )
)
