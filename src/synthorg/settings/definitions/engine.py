# module-kind: declarative
"""Engine namespace setting definitions."""

from synthorg.core.completion_enums import (
    REASONING_EFFORT_ORDER,
    REASONING_UNSET,
)
from synthorg.core.task_enums import STAKES_ORDER
from synthorg.core.types import CAPABILITY_LADDER
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="agent_max_response_tokens",
        type=SettingType.INTEGER,
        default="32768",
        description=(
            "Output ceiling for ONE agent response, covering the hidden"
            " reasoning a model emits before it writes anything. An agent that"
            " sets its own max_tokens keeps it; this answers for every agent"
            " that does not, which is all of them by default. Sized for a"
            " reasoning model: below roughly 8k such a model can spend the"
            " whole budget thinking, emit no tool call, and be recorded as"
            " having finished its work, because a turn with no tool call is"
            " how a session reports completion. A ceiling costs nothing when"
            " unused, so setting it low buys no saving and risks that silence."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        min_value=1024,
        max_value=200_000,
    )
)

# ── Work-loop streaming ──────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="work_loop_streaming_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Stream each per-turn LLM call in the task-execution loop so an"
            " operator can cancel or redirect an in-flight call mid-turn,"
            " instead of only at the turn boundary. Gated per run on the"
            " model's streaming support; falls back to a non-streaming call"
            " otherwise. Resolved live per run, so a change applies without a"
            " restart."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
    )
)

# ── Blocking delegation ──────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="delegation_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Expose the blocking delegate_and_await tool so an agent can"
            " offload a focused sub-task to another agent, run it to"
            " completion inline, and consume its transcript in the same"
            " turn. Resolved live per delegation call, so a change applies"
            " without a restart."
        ),
        group="Delegation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="delegation_max_turns",
        type=SettingType.INTEGER,
        default="10",
        description=(
            "Hard cap on the number of LLM turns a delegated child agent"
            " may take. Bounds the cost and latency of a single"
            " delegate_and_await call; read live per delegation."
        ),
        group="Delegation",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=200,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="delegation_max_depth",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "Maximum depth of the parent-task chain a delegate_and_await call"
            " may sit at. Bounds nested delegation so a chain of agents"
            " delegating to one another cannot recurse without limit; a"
            " delegation whose target already appears as an ancestor's"
            " assignee (a cycle, including self-delegation) is refused"
            " regardless. Read live per delegation."
        ),
        group="Delegation",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=20,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="delegation_timeout_seconds",
        type=SettingType.FLOAT,
        default="0.0",
        description=(
            "Wall-clock ceiling for a single delegated child run. Because a"
            " delegate_and_await call blocks the supervisor's own turn, a"
            " child that stalls on a slow provider would otherwise hold the"
            " parent open indefinitely. 0 means no wall-clock limit (bounded"
            " only by delegation_max_turns). Read live per delegation."
        ),
        group="Delegation",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=3600.0,
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
        key="max_rework_rounds",
        type=SettingType.INTEGER,
        default="2",
        description=(
            "Consecutive times a review may send one dispatch's work back"
            " before the task fails instead of being re-run. Each round is a"
            " whole run, so this bounds spend as much as progress: a model"
            " told twice why its work was refused and returning it unchanged"
            " is unlikely to answer a third time. Past the bound the run"
            " fails, which is re-runnable and visible, rather than resting in"
            " a status nothing watches. Read live per dispatch."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=10,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="auto_review_on_completion",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Automatically run the staged review pipeline when an agent"
            " completes a task (reaching IN_REVIEW), applying its verdict"
            " without waiting for a human to open the review. On by default so"
            " a verified task self-completes autonomously and the completion"
            " oracle gates every autonomous completion; turn it off to require"
            " a human to open each review. Hot-reloadable: a change rebuilds the"
            " runtime pipeline on the next task, no restart."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="review_artifact_max_chars_per_file",
        type=SettingType.INTEGER,
        default="20000",
        description=(
            "How much of each declared artifact the completion oracle's peer"
            " reviewer and the red-team gate are shown. The reviewer judges the"
            " files the task promised, not the agent's closing message about"
            " them, and a prompt is a fixed budget: one large generated file"
            " would otherwise crowd out every other deliverable. Content past"
            " the bound is cut with a visible truncation marker, so the reviewer"
            " knows it is judging an excerpt. Read live per review."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
        min_value=1000,
        max_value=500000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="review_artifact_max_chars_total",
        type=SettingType.INTEGER,
        default="60000",
        description=(
            "Total content across every declared artifact shown to a reviewer."
            " Once reached, the remaining artifacts are named as omitted rather"
            " than dropped silently, so a reviewer of a large deliverable knows"
            " what it did not see. Read live per review."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
        min_value=1000,
        max_value=2000000,
    )
)

# ── Mid-task clarification ──────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="clarification_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Let an executing agent pause to ask a human a question via the"
            " request_clarification tool, parking its context and moving the"
            " task to AWAITING_INPUT until the human answers, then resuming with"
            " the answer injected. On by default: an agent that cannot proceed"
            " without a human's answer should ask rather than guess, because a"
            " wrong guess on an ambiguous requirement costs more than the pause,"
            " and the question is answerable in the unified conversation. Turn"
            " it off to make agents always proceed on their own judgement. The"
            " tool is added to every agent toolset, which applies on the next"
            " runtime rebuild the change triggers."
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
        default="true",
        description=(
            "Let a lead agent surface project-shaping decisions to a human via"
            " the request_project_decision tool: it parks the run (like a"
            " clarification), and on the human's answer records a DECISION"
            " entry in the project brain and resumes with the choice injected."
            " On by default: a fork that shapes the project belongs to the"
            " operator, and the recorded decision is what later work is held to."
            " Turn it off to let agents settle project-shaping forks themselves."
            " The tool is added to every agent toolset, which applies on the"
            " next runtime rebuild the change triggers."
        ),
        group="Scoping",
        level=SettingLevel.ADVANCED,
    )
)

# ── Ask policy (the standing "ask rather than guess" directive) ─

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="ask_policy_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Inject the standing 'ask rather than guess' directive into every"
            " agent system prompt, worded for whichever autonomy level the run"
            " resolves to and at the verbosity the prompt profile selects. The"
            " directive tells an agent to put a choice to a human when it is"
            " material (it moves cost, scope, public behaviour, data, or someone"
            " else's work) and hard to reverse, and to decide everything else"
            " itself. Applies on the next prompt build, no restart."
        ),
        group="Ask Policy",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="ask_policy_extra_directives",
        type=SettingType.JSON,
        default="[]",
        description=(
            "Additional 'when to ask' directives appended below the standing"
            " one, as a JSON array of {id, text, scope, scope_kind} objects."
            " scope_kind is 'all', 'role', or 'department'; an agent sees every"
            " 'all' directive plus the role and department directives matching"
            " it. Use this to name the choices your organisation always wants"
            " escalated (a schema change, a public API break, spend above a"
            " threshold). Applies on the next prompt build, no restart."
        ),
        group="Ask Policy",
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

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="evolution_proposer_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the evolution proposers analyse agent"
            " performance on. A model reference (`{provider, model_id}`)"
            " because a provider is a registered connection with its own"
            " credentials and endpoint, so a bare model id names no dispatch"
            " target. Unset leaves the LLM proposers unwired: evolution then"
            " proposes nothing rather than analysing on a connection nobody"
            " chose."
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
            " authority cues are detected in the conversation). Applies on"
            " the next runtime rebuild, which the change itself triggers."
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

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="routing_low_confidence_score",
        type=SettingType.FLOAT,
        default="0.35",
        description=(
            "Score below which a winning agent-task fit is low-confidence."
            " A below-band fit is always assigned (the best available agent is"
            " never a hard-fail, so the organisation does not deadlock), but it"
            " is flagged: high/critical-stakes assignments log an operator-facing"
            " escalation (WARNING) for review while the work proceeds. Should be"
            " >= routing_min_score."
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
# two ``matcher_*_min_context`` thresholds derive the report-only rung.

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
        key="matcher_expert_min_context",
        type=SettingType.INTEGER,
        default="200000",
        description=(
            "Model matcher: minimum context window (tokens) for a model"
            " to derive the report-only 'expert' rung."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=1,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_capable_min_context",
        type=SettingType.INTEGER,
        default="32000",
        description=(
            "Model matcher: minimum context window (tokens) for a model"
            " to derive the report-only 'capable' rung (below this"
            " is 'basic')."
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

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_prefer_local",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Model matcher: when a locally-hosted model already meets a role's"
            " capability demand, prefer it over a remote/cloud model in the same"
            " band, so free local hardware is used before paid cloud. Only"
            " affects demand-tier auto-assignment; an explicit model pin"
            " (model_id/family/pattern) is unaffected. Applied on the next"
            " runtime-services rebuild, triggered by a settings subscriber, so a"
            " change takes effect without a restart."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="matcher_min_cloud_cost_tier",
        type=SettingType.INTEGER,
        default="2",
        description=(
            "Model matcher: the lowest cost tier (1=economy .. 4=frontier)"
            " an agent may be auto-assigned on a remote/cloud provider, so a paid"
            " provider never draws a bottom-tier model when a role could take a"
            " stronger one. Locally-hosted providers are exempt (free to run),"
            " and an explicit model pin (model_id/family/pattern) is unaffected."
            " Applied on the next runtime-services rebuild, triggered by a"
            " settings subscriber, so a change takes effect without a restart."
        ),
        group="Model Matcher",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=4,
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
        key="tool_output_max_chars",
        type=SettingType.INTEGER,
        default="24000",
        description=(
            "Ceiling on the characters of a tool's own output one result may"
            " put into the conversation; the fence the loop wraps every"
            " result in is the system's own bytes and is not counted."
            " A result is resent on every later turn of the"
            " run, so its size is paid once per turn for the rest of the"
            " session; abbreviating at the boundary where it enters the"
            " conversation stops that at source, where compaction would"
            " only recover it later. The head and tail are kept and an"
            " elision marker states how much was dropped, so the agent can"
            " narrow its next call rather than assume it saw everything."
            " Read live on every tool turn. 0 disables the ceiling; a value"
            " between 1 and 255 is applied as 256, the smallest ceiling the"
            " marker fits inside."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=1_000_000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="max_turns",
        type=SettingType.INTEGER,
        default="300",
        description=(
            "Turn budget a run starts with, not the last word on how long it"
            " may go: reaching it buys another budget while"
            " engine.max_turn_extensions remain AND the budget just spent"
            " called a tool, so a working run can reach"
            " max_turns * (1 + max_turn_extensions) turns while one going in"
            " circles stops here. Applied by AgentEngine.run when a caller"
            " does not pass an explicit max_turns. The money backstop is"
            " budget.run_hard_ceiling, enforced in-loop whenever a budget"
            " enforcer is wired, which the shipped boot path does whenever"
            " persistence connects and cost-dial wiring succeeds; the"
            " stagnation detector (engine.stagnation_strategy, tool"
            " repetition by default) ends a run going in circles earlier"
            " than this cap does."
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
        key="max_unresolved_tool_turns",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "How many consecutive turns a run may spend asking only for tools"
            " that are not registered before it is stopped. Such a turn ran"
            " nothing, so it made no progress whatever its arguments were, and"
            " the registry answers the first one by name with its nearest"
            " matches: a run that has not taken them by the fifth attempt is"
            " not going to. This is the early stop; engine.max_turns is the"
            " late one, and the stagnation detector answers a different"
            " question (is the SAME call repeated) that drifting arguments"
            " defeat. Zero disables it. Read live, per run."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="max_turn_extensions",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "How many further turn budgets a run may grant itself before it"
            " parks for a human, each worth engine.max_turns again, so the"
            " effective ceiling is max_turns * (1 + this). Reaching the cap"
            " usually means the work was bigger than the estimate, so the"
            " common case carries on rather than interrupting anyone; an"
            " extension goes only to a run that called a tool in the budget"
            " it just spent, so idling does not buy more turns. Once these"
            " are spent the run parks with its workspace intact and asks"
            " whether to continue. Zero ends every run at its first ceiling."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=20,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="budget_signal_step_percent",
        type=SettingType.INTEGER,
        default="25",
        description=(
            "How often, in percent of a run's token ceiling, the agent is"
            " told how much of its budget it has used: once each time"
            " accumulated spend crosses a further step. A run with no token"
            " ceiling gets no signal at all, and the ceiling itself is"
            " declared once, in the system prompt, at zero spend. Zero"
            " disables the per-turn signal entirely, leaving only the"
            " terminal warning at engine.budget_signal_terminal_percent."
            " Read live, per run."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="budget_signal_terminal_percent",
        type=SettingType.INTEGER,
        default="90",
        description=(
            "Share of a run's token ceiling past which the budget signal"
            " fires on every turn rather than at engine.budget_signal_step_"
            "percent's intervals, with wording naming what happens when the"
            " ceiling is reached. Read live, per run."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="produce_early_percent",
        type=SettingType.INTEGER,
        default="50",
        description=(
            "Share of a work task's token ceiling a session may spend before"
            " having produced anything earns a corrective nudge, distinct"
            " from the zero-artifact guard that only judges a finished run:"
            " this fires while the session is still alive, so it can still"
            " act. Fires once per run. Zero disables it. Read live, per run."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="task_engine_max_queue_size",
        type=SettingType.INTEGER,
        default="1000",
        description=(
            "Admission cap on the in-process task-mutation queue."
            " ``0`` means unbounded. Raise for high-throughput deployments"
            " with many concurrent agents; lower for resource-constrained"
            " hosts. Applies to the next submitted mutation; lowering it"
            " below the current depth lets the backlog drain rather than"
            " discarding mutations already accepted."
        ),
        group="Execution",
        level=SettingLevel.ADVANCED,
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
        level=SettingLevel.ADVANCED,
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
        level=SettingLevel.ADVANCED,
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
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100,
    )
)

# ── Agile Sprints ───────────────────────────────────────────────
# Gates the sprint service for agile_kanban orgs: when on, delivery
# creates a real sprint, pulls tasks into its backlog, and advances the
# lifecycle as the backlog is delivered. Read per task-state event so a
# runtime change applies to the next event with no restart.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="sprint_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Run real agile sprints for an 'agile_kanban' org: create + advance"
            " a sprint as delivery proceeds, pull tasks into its backlog, and"
            " gate board moves to backlog tasks. No effect for other workflow"
            " types. Read per task-state event so a runtime change applies to"
            " the next event with no restart."
        ),
        group="Agile Sprints",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="sprint_tail_resync_interval_seconds",
        type=SettingType.FLOAT,
        default="600.0",
        description=(
            "Cadence of the sprint recovery sweep, which advances sprints"
            " whose lifecycle hop was lost with the process that was making"
            " it: a sprint whose backlog was fully delivered but which never"
            " reached review has no completion left to re-fire it. A restart"
            " runs the sweep immediately, so this cadence covers the other"
            " case: a hop lost without its process. Re-read per tick, so a"
            " change applies with no restart."
        ),
        group="Agile Sprints",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=86400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="sprint_tail_sweep_paused",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Pause flag for the sprint recovery sweep. When True the scheduler"
            " stays resident but every tick short-circuits, so no stranded"
            " sprint is advanced. It does not affect the pass a restart runs."
            " Read per tick, so it applies with no restart."
        ),
        group="Agile Sprints",
        level=SettingLevel.ADVANCED,
    )
)

# ── Initiative tail: auto-replan ─────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="auto_replan_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Let an initiative that can no longer advance replan itself. Fires"
            " when every outstanding plan item is dead (failed, rejected,"
            " cancelled, blocked, suspended, or interrupted) and none can move"
            " on its own; work awaiting a human is never treated as a stall."
            " The successor lands in review, so the operator still decides."
            " Off leaves a stalled initiative hanging until someone notices."
            " Read live per fire, so a change applies without a restart."
        ),
        group="Initiative Tail",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="auto_replan_max_generations",
        type=SettingType.INTEGER,
        default="2",
        description=(
            "How many times one initiative may replan itself before it is"
            " parked for a human. A successor opened automatically carries its"
            " predecessor's generation plus one; a human replan resets it, so"
            " only an unattended chain is capped. 0 disables the automatic"
            " replan entirely."
        ),
        group="Initiative Tail",
        min_value=0,
        max_value=10,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="auto_extension_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Let a workstream that finished its planned tree without covering"
            " its objective be handed another extension unasked, once"
            " ``coordination.jit_extension_planning_enabled`` has switched the"
            " mechanism on at all. This is the automatic-authority switch on"
            " the same shape as ``auto_replan_enabled``: off leaves such a"
            " workstream's gap unaddressed rather than grafting past a"
            " generation an operator has not sanctioned. Read live per fire."
        ),
        group="Initiative Tail",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="auto_extension_max_generations",
        type=SettingType.INTEGER,
        default="2",
        description=(
            "How many extensions one workstream may be handed automatically"
            " before it is left as-is with its scope unmet. Counted per"
            " workstream, not per plan, since workstreams progress"
            " independently; a workstream past its cap is not retried, on"
            " the same reasoning ``auto_replan_max_generations`` caps an"
            " unattended replan chain. 0 disables the automatic graft"
            " entirely."
        ),
        group="Initiative Tail",
        min_value=0,
        max_value=10,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="auto_extension_timeout_seconds",
        type=SettingType.FLOAT,
        default="600.0",
        description=(
            "Wall-clock ceiling on one extension's decomposition and graft,"
            " on the same shape ``auto_replan_timeout_seconds`` bounds a"
            " re-plan. A hung attempt is abandoned at this point rather than"
            " occupying a background slot; the leaf still needs an extension,"
            " and the next rollup pass re-fires the trigger."
        ),
        group="Initiative Tail",
        level=SettingLevel.ADVANCED,
        min_value=30.0,
        max_value=3600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="auto_replan_timeout_seconds",
        type=SettingType.FLOAT,
        default="600.0",
        description=(
            "Wall-clock ceiling on one automatic re-plan, which re-decomposes"
            " the objective through the planning strategy. A hung planning"
            " session is abandoned at this point rather than occupying a"
            " background slot; the stall persists and the next rollup event"
            " re-fires the trigger."
        ),
        group="Initiative Tail",
        level=SettingLevel.ADVANCED,
        min_value=30.0,
        max_value=3600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="skeleton_stage_timeout_seconds",
        type=SettingType.FLOAT,
        default="1800.0",
        description=(
            "Wall-clock ceiling on minting and dispatching one skeleton job."
            " This bounds the dispatch, not the writing of the contract, which"
            " runs as an ordinary task under the pipeline's own budgets."
        ),
        group="Initiative Head",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=7200.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="integration_stage_timeout_seconds",
        type=SettingType.FLOAT,
        default="1800.0",
        description=(
            "Wall-clock ceiling on minting and dispatching one integration"
            " job. This bounds the dispatch, not the assembly work itself,"
            " which runs as an ordinary task under the pipeline's own budgets."
        ),
        group="Initiative Tail",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=7200.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="evaluation_session_max_turns",
        type=SettingType.INTEGER,
        default="10",
        description=(
            "Turn cap for the session that scores a delivered initiative"
            " against its objective's success criteria. The lead reads the"
            " workspace and checks each criterion before submitting a verdict,"
            " so it needs more turns than a single-shot judgement."
        ),
        group="Initiative Tail",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="evaluation_session_cost_ceiling",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "Per-session spend ceiling (base currency) for the evaluation"
            " session; it halts once accumulated cost reaches this. A halted"
            " session submits no verdict, and the initiative stays parked"
            " rather than completing."
        ),
        group="Initiative Tail",
        level=SettingLevel.ADVANCED,
        min_value=0.01,
        max_value=100.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="evaluation_session_timeout_seconds",
        type=SettingType.FLOAT,
        default="300.0",
        description=(
            "Wall-clock ceiling for one evaluation. A backstop to the session's"
            " own turn and cost caps: a hung judgement is abandoned rather than"
            " occupying a background slot, and the initiative stays at"
            " evaluating until the next event re-fires the stage."
        ),
        group="Initiative Tail",
        level=SettingLevel.ADVANCED,
        min_value=30.0,
        max_value=3600.0,
    )
)

# ── Review staffing sweep ────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="review_staffing_resync_interval_seconds",
        type=SettingType.FLOAT,
        default="900.0",
        description=(
            "Cadence of the review-staffing sweep, which returns tasks parked"
            " for want of a Completion Reviewer or Red Team holder once"
            " somebody holds the role. A roster change (a dashboard edit, an"
            " approved hire, a config load) nudges the sweep to run early, but"
            " that is a latency optimisation only: this cadence is the"
            " guarantee, so a route that never notifies costs one tick rather"
            " than the release. Re-read per tick, so a change applies with no"
            " restart."
        ),
        group="Review Staffing",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=86400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="review_staffing_sweep_paused",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Pause flag for the review-staffing sweep. When True the"
            " scheduler stays resident but every tick short-circuits, so no"
            " parked task is released, no hire is opened and no notification"
            " fires. The sweep drives task transitions and hiring approvals,"
            " so this is the switch that stops it during an incident without"
            " taking the process down. Read per tick, so it applies with no"
            " restart."
        ),
        group="Review Staffing",
        level=SettingLevel.ADVANCED,
    )
)

# ── Run recovery sweep ───────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="run_recovery_resync_interval_seconds",
        type=SettingType.FLOAT,
        default="600.0",
        description=(
            "Cadence of the run-recovery sweep, which resumes initiatives"
            " whose dispatch no longer has anything driving it and requeues"
            " the tasks that dispatch left mid-flight. A restart runs the"
            " sweep immediately, so this cadence covers the other case: a"
            " dispatch lost without its process. Re-read per tick, so a change"
            " applies with no restart."
        ),
        group="Run Recovery",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=86400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="run_recovery_sweep_paused",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Pause flag for the run-recovery sweep. When True the scheduler"
            " stays resident but every tick short-circuits, so no stranded"
            " initiative is resumed and no orphaned task is requeued. Resuming"
            " starts agents, which spends, so this is the switch that stops it"
            " during an incident without taking the process down. It does not"
            " affect the pass a restart runs. Read per tick, so it applies"
            " with no restart."
        ),
        group="Run Recovery",
        level=SettingLevel.ADVANCED,
    )
)

# ── Capability policy ───────────────────────────────────────────
# What each stakes level demands of whoever takes the work. Selection walks a
# ladder toward the required rung (exact match, else the nearest above, else
# the nearest below) and NOTHING rewrites an agent's bound (provider, model)
# pair, so these knobs decide which agent is offered the work rather than
# which model runs behind one agent's name. Re-resolved live by
# ``CapabilityPolicySettingsSubscriber``, so an edit applies to the next
# assignment with no restart.

# Written out rather than derived because the settings gate resolves each key
# statically and a comprehension leaves it nothing to read; the guards below
# fail at import if a vocabulary gains a member these miss, so the dashboard
# cannot silently stop offering one.
_CAPABILITY_RUNGS = ("basic", "capable", "expert")
_STAKES_LEVELS = ("low", "normal", "high", "critical")
_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")

if _CAPABILITY_RUNGS != CAPABILITY_LADDER:
    _rungs_msg = f"_CAPABILITY_RUNGS out of sync with {CAPABILITY_LADDER}"
    raise RuntimeError(_rungs_msg)
if tuple(level.value for level in STAKES_ORDER) != _STAKES_LEVELS:
    _stakes_msg = f"_STAKES_LEVELS out of sync with {STAKES_ORDER}"
    raise RuntimeError(_stakes_msg)
if (
    REASONING_UNSET,
    *(effort.value for effort in REASONING_EFFORT_ORDER),
) != _REASONING_EFFORTS:
    _efforts_msg = f"_REASONING_EFFORTS out of sync with {REASONING_EFFORT_ORDER}"
    raise RuntimeError(_efforts_msg)


def _register_capability_floor(*, key: str, default: str, stakes: str) -> None:
    """Register the capability floor one stakes level demands."""
    _r.register(
        SettingDefinition(
            namespace=SettingNamespace.ENGINE,
            key=key,
            type=SettingType.ENUM,
            default=default,
            enum_values=_CAPABILITY_RUNGS,
            description=(
                f"Minimum model capability {stakes}-stakes work demands of the"
                " agent that takes it. Substantial complexity (complex, epic)"
                " raises it one further rung. The floors must not invert the"
                " stakes ladder: low <= normal <= high <= critical."
            ),
            group="Capability Policy",
            level=SettingLevel.ADVANCED,
        )
    )


_register_capability_floor(key="capability_floor_low", default="basic", stakes="low")
_register_capability_floor(
    key="capability_floor_normal", default="capable", stakes="normal"
)
_register_capability_floor(key="capability_floor_high", default="expert", stakes="high")
_register_capability_floor(
    key="capability_floor_critical", default="expert", stakes="critical"
)


def _register_reasoning_effort(*, key: str, default: str, stakes: str) -> None:
    """Register the reasoning depth one stakes level asks the model for."""
    _r.register(
        SettingDefinition(
            namespace=SettingNamespace.ENGINE,
            key=key,
            type=SettingType.ENUM,
            default=default,
            enum_values=_REASONING_EFFORTS,
            description=(
                f"How hard the model is asked to think on {stakes}-stakes"
                " work. 'none' leaves the provider default. This tunes how the"
                " bound model works, never which model runs. Dropped at the"
                " driver boundary for a model without reasoning support. The"
                " efforts must not invert the stakes ladder."
            ),
            group="Capability Policy",
            level=SettingLevel.ADVANCED,
        )
    )


_register_reasoning_effort(key="reasoning_effort_low", default="low", stakes="low")
_register_reasoning_effort(
    key="reasoning_effort_normal", default="low", stakes="normal"
)
_register_reasoning_effort(key="reasoning_effort_high", default="medium", stakes="high")
_register_reasoning_effort(
    key="reasoning_effort_critical", default="high", stakes="critical"
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="red_team_min_stakes",
        type=SettingType.ENUM,
        default="high",
        enum_values=_STAKES_LEVELS,
        description=(
            "Lowest stakes level whose deliverable must pass the adversarial"
            " red-team gate before it can complete. Lowering this widens what"
            " gets attacked before it ships; raising it narrows the gate."
        ),
        group="Capability Policy",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="capability_park_min_stakes",
        type=SettingType.ENUM,
        default="high",
        enum_values=_STAKES_LEVELS,
        description=(
            "Lowest stakes level at which work PARKS for an operator when no"
            " agent runs at or above the rung it demands. Below this the"
            " nearest weaker agent takes it and the concession is logged;"
            " at or above it the work waits, because measured runs show"
            " complex work failing outright on a weaker model rather than"
            " degrading."
        ),
        group="Capability Policy",
        level=SettingLevel.ADVANCED,
    )
)
