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
        key="leaf_subtask_threshold",
        type=SettingType.INTEGER,
        default="1",
        description=(
            "Maximum expected-artifact count for a sequential task to"
            " still route to a single agent (leaf) under the"
            " 'leaf-threshold' routing policy; larger work is split"
            " across a team."
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

# ── Multi-agent replan escalation caps ──────────────────────────
# Mirror the ``CoordinationConfig`` model defaults (max_stall_count=3,
# max_reset_count=2) so a config built from scratch by the resolver and
# one built from the model default observe the same escalation budget.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="max_stall_count",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Maximum consecutive stalls the coordinator tolerates before"
            " escalating / replanning a multi-agent run. Resolved per run"
            " so a runtime change applies to the next coordination."
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
        key="max_reset_count",
        type=SettingType.INTEGER,
        default="2",
        description=(
            "Maximum replan cycles the coordinator performs before"
            " escalating a stuck multi-agent run. Resolved per run so a"
            " runtime change applies to the next coordination."
        ),
        group="Concurrency",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=20,
    )
)

# ── Multi-agent middleware pipeline + strategy seams ────────────
# The coordination middleware chain is on by default (richer multi-agent
# coordination out of the box); it is built at coordinator construction,
# so a change applies on the next coordinator rebuild (restart-required).
# ``replan_strategy`` / ``orchestrator_strategy`` are no-op-by-default
# discriminators selected at coordinator build (replan) / dispatch
# (orchestrator).

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="enable_coordination_middleware",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Build and run the coordination middleware pipeline"
            " (task/progress ledgers, plan-review gate, replan hook)."
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
        key="replan_strategy",
        type=SettingType.ENUM,
        default="noop",
        enum_values=("noop", "magentic"),
        description=(
            "Replan hook the coordination middleware pipeline runs."
            " 'noop' (default) never replans; 'magentic' triggers"
            " stall-driven replans up to max_stall_count / max_reset_count."
            " Applied on the next coordinator rebuild."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="orchestrator_strategy",
        type=SettingType.ENUM,
        default="naive",
        enum_values=("naive", "magentic_dynamic"),
        description=(
            "Subtask-selection strategy for the centralized wave"
            " dispatcher. 'naive' (default) dispatches all subtasks in"
            " order; 'magentic_dynamic' prioritises blocked subtasks when"
            " a progress ledger is present. Resolved per run."
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
