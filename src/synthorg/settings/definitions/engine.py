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

# ── Automatic review ────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="auto_review_on_completion",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Automatically run the staged review pipeline when an agent"
            " completes a task (reaching IN_REVIEW), applying its verdict"
            " without waiting for a human to open the review. Off by default"
            " (a human decides at the review gate); when on, the pipeline is"
            " wired into the agent runtime at boot, so a change applies on the"
            " next runtime-services rebuild (provider re-init)."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
    )
)

# ── Mid-task clarification ──────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="clarification_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Let an executing agent pause to ask a human a question via the"
            " request_clarification tool, parking its context and moving the"
            " task to AWAITING_INPUT until the human answers, then resuming with"
            " the answer injected. Off by default (agents proceed on their own"
            " judgement); when on, the tool is added to every agent toolset at"
            " boot, so a change applies on the next runtime-services rebuild."
        ),
        group="Clarification",
        level=SettingLevel.ADVANCED,
    )
)

# ── Scoping + decision gate ─────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="scoping_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Let a lead agent surface project-shaping decisions to a human via"
            " the request_project_decision tool: it parks the run (like a"
            " clarification), and on the human's answer records a DECISION"
            " entry in the project brain and resumes with the choice injected."
            " Off by default (agents decide on their own); when on, the tool is"
            " added to every agent toolset at boot, so a change applies on the"
            " next runtime-services rebuild."
        ),
        group="Scoping",
        level=SettingLevel.ADVANCED,
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
            " Applied on the next runtime-services rebuild, triggered by"
            " a settings subscriber, so a change takes effect without a"
            " restart."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
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
            " falls back to heuristic (no rule matched). Applied on the"
            " next runtime-services rebuild, triggered by a settings"
            " subscriber, so a change takes effect without a restart."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
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
            " Applied on the next runtime-services rebuild, triggered by"
            " a settings subscriber, so a change takes effect without a"
            " restart."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
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
            " leave enabled in production. Mutable kill-switch: the"
            " ``engine_timeout`` context manager reads a process cache"
            " per coroutine entry, and a settings subscriber pushes the"
            " new value into the cache, so a change applies without a"
            " restart."
        ),
        group="Safety",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="enable_agent_middleware",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether the agent middleware chain is wired into the engine."
            " When enabled, its before_agent / after_agent hooks fire at"
            " the execution boundary; the live effect is authority-"
            " deference defence (a justification header is injected when"
            " authority cues are detected in the conversation). Baked in"
            " at process startup."
        ),
        group="Safety",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="shutdown_grace_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Seconds the graceful-shutdown strategy waits for a"
            " cooperative agent exit before escalating."
        ),
        group="Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="shutdown_cleanup_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Seconds allowed for shutdown cleanup callbacks to run after"
            " the agent has stopped."
        ),
        group="Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=60.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="memory_context_token_budget",
        type=SettingType.INTEGER,
        default="2000",
        description=(
            "Token cap for memories injected into an agent's"
            " pre-execution context, so the memory section cannot crowd"
            " out the system prompt and task instruction."
        ),
        group="Memory",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="task_assignment_max_concurrent_tasks_per_agent",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "Maximum concurrent tasks an agent is intended to handle;"
            " scoring-based assignment strategies filter out agents at"
            " capacity."
        ),
        group="Task Assignment",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
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
# Capability-aware composite: ``matcher_base_score`` is the floor for a
# candidate that clears the hard capability filters; ``matcher_capability_fit_weight``
# rewards models carrying more capabilities; ``matcher_headroom_max_bonus``
# (clamped by ``matcher_headroom_ratio_cap``) credits context headroom;
# ``matcher_priority_max_bonus`` ranks on the absolute priority axis. The
# two ``tier_*_min_context`` thresholds derive the report-only tier label.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_base_score",
        type=SettingType.FLOAT,
        default="0.4",
        description=(
            "Model matcher: floor score awarded when a candidate clears"
            " the hard capability filters, before capability-fit,"
            " headroom, and priority bonuses are applied."
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
        key="matcher_capability_fit_weight",
        type=SettingType.FLOAT,
        default="0.2",
        description=(
            "Model matcher: maximum bonus from the fraction of known"
            " capabilities (tools / vision / reasoning) a model"
            " supports. Rewards more capable models as a tiebreaker."
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
        default="0.2",
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
        default="0.2",
        description=(
            "Model matcher: maximum bonus from the absolute priority-axis"
            " ranking among candidates (cost / quality / speed / balanced)."
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
        key="matcher_tier_large_min_context",
        type=SettingType.INTEGER,
        default="200000",
        description=(
            "Model matcher: minimum context window (tokens) for a model"
            " to derive the report-only 'large' tier label."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=1,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_tier_medium_min_context",
        type=SettingType.INTEGER,
        default="32000",
        description=(
            "Model matcher: minimum context window (tokens) for a model"
            " to derive the report-only 'medium' tier label (below this"
            " is 'small')."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=1,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_min_usable_parameters",
        type=SettingType.INTEGER,
        default="14000000000",
        description=(
            "Model matcher: smallest parameter count a model may have to be"
            " auto-assigned to an agent. Smaller models cannot reliably run an"
            " agent loop, so the demand path excludes them; an explicit"
            " family/pattern/id reference still honours them. Applied on"
            " the next runtime-services rebuild, triggered by a settings"
            " subscriber, so a change takes effect without a restart."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=0,
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

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="health_monitoring_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether the two-layer agent-health monitoring pipeline runs"
            " after each engine execution. When active the judge emits"
            " escalation tickets, the triage filter dismisses or escalates"
            " them, and escalated tickets are dispatched as health"
            " notifications. Read per run so it can be toggled live."
        ),
        group="Health",
        level=SettingLevel.ADVANCED,
    )
)

# ── Workflow board ──────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="workflow_type",
        type=SettingType.ENUM,
        default="agile_kanban",
        enum_values=(
            "sequential_pipeline",
            "parallel_execution",
            "kanban",
            "agile_kanban",
        ),
        description=(
            "The org's declared delivery workflow. 'kanban' / 'agile_kanban'"
            " render a WIP-limited board; the value is surfaced on the board"
            " view. Read per board request so a runtime change applies to the"
            " next board operation with no restart."
        ),
        group="Kanban Board",
        level=SettingLevel.ADVANCED,
    )
)

# ── Kanban Board WIP limits ─────────────────────────────────────
# The board projects tasks onto columns (backlog / ready / in-progress /
# review / done) via STATUS_TO_COLUMN; these knobs cap how much work sits
# in the flow-limited columns. Read per board request / move so a runtime
# change applies to the next board operation with no restart. Off by
# default (advisory): counts + over-limit are surfaced but human moves are
# not blocked until an operator opts into enforcement.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="kanban_enforce_wip",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Enforce Kanban WIP limits on human board moves: when set, a"
            " drag-drop that would push a column over its limit is rejected."
            " Off by default (advisory) so the board surfaces over-limit"
            " columns without blocking. Read per board move so a runtime"
            " change applies to the next move with no restart."
        ),
        group="Kanban Board",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="kanban_wip_in_progress",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "Work-in-progress limit for the in-progress column: the maximum"
            " number of tasks actively being worked before the column is"
            " over-limit. Read per board request so a runtime change applies"
            " to the next board operation."
        ),
        group="Kanban Board",
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="kanban_wip_review",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Work-in-progress limit for the review column: the maximum number"
            " of tasks awaiting review before the column is over-limit. Read"
            " per board request so a runtime change applies to the next board"
            " operation."
        ),
        group="Kanban Board",
        min_value=1,
        max_value=100,
    )
)
