# module-kind: declarative
"""Coordination namespace setting definitions."""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="max_concurrency_per_wave",
        type=SettingType.INTEGER,
        default="5",
        description="Maximum number of agents in a single execution wave",
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="fail_fast",
        type=SettingType.BOOLEAN,
        default="false",
        description="Stop on first wave failure instead of continuing",
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="enable_workspace_isolation",
        type=SettingType.BOOLEAN,
        default="true",
        description="Create isolated workspaces for multi-agent execution",
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="devcontainer_context_max_bytes",
        type=SettingType.INTEGER,
        default="536870912",
        description=(
            "Ceiling on the uncompressed size of the build context uploaded"
            " to the Docker daemon when a project declares a 'devcontainer'"
            " environment. The context is an agent-writable workspace and is"
            " packed in the backend process, so an agent that writes a large"
            " artefact into it would otherwise exhaust the memory that also"
            " hosts the API, auth and the settings store. A build whose"
            " context exceeds this is refused with the size in the message"
            " rather than attempted. Read live: a change applies to the next"
            " build with no restart."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=1048576,
        max_value=8589934592,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="base_branch",
        type=SettingType.STRING,
        default="main",
        description="Git branch for workspace isolation",
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="decomposition_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the coordinator's task decomposition strategy"
            " and the llm-judged routing policy invoke. A model reference"
            " (`{provider, model_id}`) so the model resolves against the"
            " provider it was selected on, not the first registered one."
            " Required whenever a provider is configured: a provider-present"
            " boot builds the coordinator eagerly and validates this value,"
            " raising a startup error when it is unset. Resolved at boot; a"
            " runtime change applies on the next coordinator rebuild"
            " (provider re-init)."
        ),
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="decomposition_strategy",
        type=SettingType.ENUM,
        default="agent-session",
        enum_values=("agent-session", "llm"),
        description=(
            "How the coordinator turns a greenlit objective into a plan."
            " 'agent-session' (default) runs a bounded planning session as the"
            " staffed owner: the owner reasons across turns, researches with"
            " any read-only tools it is granted (none are wired by default"
            " until a decomposition tool provider is supplied), drafts subtasks"
            " with per-item expected artifacts and acceptance criteria,"
            " self-reviews, then submits the plan (falling back to a single LLM"
            " call when no owner is staffed or no plan is submitted). 'llm' uses"
            " one structured LLM call. Resolved at boot; a runtime change"
            " applies on the next coordinator rebuild (provider re-init)."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="decomposition_agent_max_turns",
        type=SettingType.INTEGER,
        default="12",
        description=(
            "Hard turn cap for the 'agent-session' decomposer's owner-run"
            " planning loop: the maximum number of research/self-review turns"
            " before the owner must submit a plan. Ignored by the 'llm'"
            " strategy. Resolved at boot; a runtime change applies on the next"
            " coordinator rebuild (provider re-init)."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="decomposition_agent_cost_ceiling",
        type=SettingType.FLOAT,
        default="2.0",
        description=(
            "Per-session spend ceiling (base currency) for the 'agent-session'"
            " decomposer's planning loop: the session halts once accumulated"
            " cost reaches it, then falls back to a single LLM call if no plan"
            " was submitted. Ignored by the 'llm' strategy. Resolved at boot; a"
            " runtime change applies on the next coordinator rebuild (provider"
            " re-init)."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=0.01,
        max_value=100.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="routing_policy",
        type=SettingType.ENUM,
        default="llm-judged",
        enum_values=("leaf-threshold", "always-team", "llm-judged"),
        description=(
            "Work pipeline solo-vs-team routing policy. 'llm-judged'"
            " (default) asks the decomposition model whether a brief needs"
            " a team, falling back to the leaf-threshold heuristic on model"
            " error; 'leaf-threshold' classifies small sequential work as"
            " single-agent by expected-artifact count; 'always-team' forces"
            " the coordinator. Resolved at boot; a runtime change applies on"
            " the next pipeline rebuild (provider re-init)."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_approval_required",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Gate splittable team work on human plan approval: when set, the"
            " coordinator decomposes the brief into a plan and parks it for"
            " approval before any agent builds; the approved plan is then"
            " dispatched verbatim (no re-decomposition). On by default, so"
            " every greenlit initiative (a charter or a conversational work"
            " brief) parks a plan for holistic review before anything is built."
            " Turn off to dispatch team work straight to the coordinator."
            " Applied on the next runtime-services rebuild (the gate is"
            " attached at boot)."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_panel_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Run a bounded stakeholder panel over a gated plan before the human"
            " approver sees it: the relevant leads (a technical lead, a budget"
            " lead, the department heads for the domains touched, plus a senior"
            " peer, never the plan's own owner) each review the plan and their"
            " consolidated verdict is attached to it. Only applies when plan"
            " approval is gated and a provider is wired; degrades to no panel"
            " review otherwise. Applied on the next runtime-services rebuild."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_panel_size",
        type=SettingType.INTEGER,
        default="4",
        description=(
            "Maximum number of reviewers on the stakeholder plan-review panel"
            " (the coordination group bound: the relevant leads sized to the"
            " plan, not everyone). Resolved on the next runtime-services rebuild."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=8,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_panel_max_turns",
        type=SettingType.INTEGER,
        default="6",
        description=(
            "Hard turn cap for each panellist's plan-review session before it"
            " must submit a verdict. Resolved on the next runtime-services"
            " rebuild."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_max_revision_rounds",
        type=SettingType.INTEGER,
        default="2",
        description=(
            "How many times a reviewed plan may be sent back to be re-planned"
            " before it is parked for the operator regardless. Each round costs"
            " a fresh decomposition and a fresh panel, so the cap is what stops"
            " a panel and a planner that disagree from arguing indefinitely."
            " Set 0 to make the panel advisory: its findings are still recorded"
            " and shown, but nothing acts on them. The panel bakes this in when"
            " it is built, and its subsystem rebuilds on a write, so a change"
            " applies from the next reconcile pass."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=5,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_panel_cost_ceiling",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "Per-reviewer spend ceiling (base currency) for a plan-review"
            " session: the session halts once accumulated cost reaches it."
            " Resolved on the next runtime-services rebuild."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=0.01,
        max_value=100.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_reply_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "When you comment on a plan item under review, let the responsible"
            " agent (the item's owner role, else the Chief of Staff) reply"
            " inline with a grounded answer. On by default; gated live per"
            " comment, so a failed reply never blocks your comment. Turn off"
            " for a comment board no agent answers."
        ),
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_reply_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for a plan-item reply, selected through the model"
            " picker (a `{provider, model_id}` reference). Empty leaves plan"
            " replies unwired (comments are posted with no agent answer). Read"
            " live per reply."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_reply_temperature",
        type=SettingType.FLOAT,
        default="0.3",
        description=(
            "Sampling temperature for a plan-item reply. Resolved live per"
            " reply, so a change takes effect on the next reply without a"
            " restart."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=2.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_reply_max_tokens",
        type=SettingType.INTEGER,
        default="600",
        description=(
            "Token budget for one plan-item reply. Resolved live per reply, so"
            " a change takes effect on the next reply without a restart."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        min_value=100,
        max_value=4096,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="plan_review_reply_timeout_seconds",
        type=SettingType.FLOAT,
        default="120.0",
        description=(
            "Wall-clock cap for one plan-item reply call. Resolved live per"
            " reply, so a change takes effect on the next reply without a"
            " restart."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="decomposition_timeout_seconds",
        type=SettingType.FLOAT,
        default="600.0",
        description=(
            "Wall-clock ceiling on one decomposition, covering the planning"
            " session and every parse retry inside it. Without it a planner"
            " waiting on a provider that never answers holds whatever called"
            " it, which for the two request-path callers is an HTTP worker."
            " Resolved live per decomposition, so a change takes effect on the"
            " next one without a restart."
        ),
        group="Models",
        level=SettingLevel.ADVANCED,
        min_value=30.0,
        max_value=3600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="leaf_subtask_threshold",
        type=SettingType.INTEGER,
        default="1",
        description=(
            "Maximum expected-artifact count for work to still belong to a"
            " single agent. Read twice: the 'leaf-threshold' routing policy"
            " applies it to a whole objective, and recursive decomposition"
            " applies it to each planned subtask, splitting one that declares"
            " more deliverables than an agent can own."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=20,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="subtask_max_criteria",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "Maximum acceptance-criteria count for a planned subtask to still"
            " be one agent's worth of work. A subtask declaring more ways of"
            " being done is decomposed again when recursive decomposition is"
            " enabled and the depth budget allows."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=25,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="decomposition_tree_timeout_seconds",
        type=SettingType.FLOAT,
        default="3600.0",
        description=(
            "Whole-tree ceiling for one decomposition call, across every level"
            " it recurses into. Distinct from"
            " 'decomposition_timeout_seconds', which bounds a single planning"
            " session: sessions scale with the node count rather than the"
            " depth, so no multiple of the per-session number bounds a tree."
            " Two of the four callers are request handlers, and this is what"
            " keeps a deep recursion from occupying one for as long as the"
            " tree keeps branching."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=86400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="recursive_decomposition_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Decompose an oversized subtask again instead of dispatching it"
            " whole, up to the decomposition's depth budget. Ships off: a"
            " recursive plan is a tree, and the durable plan model is still"
            " flat, so only a caller that reads the decomposition tree"
            " directly (the recursion-depth harness) can act on the result."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="department_policy_cas_retry_attempts",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Maximum compare-and-swap retry attempts for the"
            " dept_ceremony_policies JSON blob.  A losing CAS writer"
            " re-reads, mutates, and re-attempts up to this many"
            " times before surfacing a VersionConflictError to the"
            " caller (HTTP 409). Resolved per mutation so a runtime"
            " change applies to the next request."
        ),
        group="Concurrency",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="company_departments_cas_retry_attempts",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Maximum compare-and-swap retry attempts for the"
            " company.departments / company.agents JSON blob (team and"
            " template-pack writers). A losing CAS writer re-reads,"
            " mutates, and re-attempts up to this many times before"
            " surfacing a VersionConflictError to the caller (HTTP 409)."
            " Resolved per mutation so a runtime change applies to the"
            " next request."
        ),
        group="Concurrency",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10,
    )
)

# ── Ceremony Policy ──────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="ceremony_strategy",
        type=SettingType.ENUM,
        default="task_driven",
        description="Ceremony scheduling strategy for sprint ceremonies",
        group="Ceremony Policy",
        # Must be kept in sync with CeremonyStrategyType members;
        # test_ceremony_settings.py verifies this.
        enum_values=(
            "task_driven",
            "calendar",
            "hybrid",
            "event_driven",
            "budget_driven",
            "throughput_adaptive",
            "external_trigger",
            "milestone_driven",
        ),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="ceremony_strategy_config",
        type=SettingType.JSON,
        default="{}",
        description="Strategy-specific configuration as JSON",
        group="Ceremony Policy",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="ceremony_velocity_calculator",
        type=SettingType.ENUM,
        default="task_driven",
        description="Velocity calculator for sprint metrics",
        group="Ceremony Policy",
        # Must be kept in sync with VelocityCalcType members;
        # test_ceremony_settings.py verifies this.
        enum_values=(
            "task_driven",
            "calendar",
            "multi_dimensional",
            "budget",
            "points_per_sprint",
        ),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="ceremony_auto_transition",
        type=SettingType.BOOLEAN,
        default="true",
        description="Automatically transition sprints when strategy conditions are met",
        group="Ceremony Policy",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="ceremony_transition_threshold",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "Fraction of tasks/time/budget that must be reached "
            "before auto-transition fires (0.01 to 1.0)"
        ),
        group="Ceremony Policy",
        min_value=0.01,
        max_value=1.0,
    )
)

# The next two settings are aggregate JSON blobs managed entirely through the
# settings service (keyed by department or ceremony name).
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="dept_ceremony_policies",
        type=SettingType.JSON,
        default="{}",
        description=(
            "Per-department ceremony policy overrides as JSON. "
            "Keys are department names, values are partial "
            "CeremonyPolicyConfig objects. Null values inherit "
            "the project-level policy."
        ),
        group="Ceremony Policy",
        level=SettingLevel.ADVANCED,
    )
)

# ── CAS optimistic-concurrency retry tuning ─────────────────────
# Fallback module constant in core/concurrency/cas_retry.py mirrors
# this default so a handler constructed without an explicit override
# observes the documented attempt budget.

# ── Multi-agent middleware pipeline ─────────────────────────────
# The coordination middleware chain is on by default (richer multi-agent
# coordination out of the box); it is built at coordinator construction,
# so a change applies on the next coordinator rebuild (restart-required).

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="enable_coordination_middleware",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Build and run the coordination middleware pipeline"
            " (task ledger, plan-review gate, authority deference)."
            " On by default. A change triggers a runtime-services rebuild"
            " via a settings subscriber (which rebuilds the coordinator)"
            " without a restart."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="max_delegation_rounds",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Soft cap on delegation rounds (the parent task's delegation"
            " chain depth) the coordinator tolerates. A warning is emitted"
            " at this limit; coordination hard-aborts at 2x. Resolved per"
            " run so a runtime change applies to the next coordination."
        ),
        group="Concurrency",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=20,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="cas_max_attempts",
        type=SettingType.INTEGER,
        default="2",
        description=(
            "Compare-and-set attempt budget for optimistic concurrency"
            " on shared mutation surfaces (departments, approval"
            " transitions). Counts the total number of attempts"
            " (including the first call); ``2`` means one retry."
        ),
        group="Concurrency",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10,
    )
)
