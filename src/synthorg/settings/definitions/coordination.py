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
        type=SettingType.STRING,
        default="example-medium-001",
        description=(
            "LLM model identifier the coordinator's task decomposition"
            " strategy invokes against the first registered provider."
            " Resolved at boot; a runtime change applies on the next"
            " coordinator rebuild (provider re-init)."
        ),
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COORDINATION,
        key="routing_policy",
        type=SettingType.ENUM,
        default="leaf-threshold",
        enum_values=("leaf-threshold", "always-team", "llm-judged"),
        description=(
            "Work pipeline solo-vs-team routing policy. 'leaf-threshold'"
            " (default) classifies small sequential work as single-agent;"
            " 'always-team' forces the coordinator; 'llm-judged' asks the"
            " decomposition model. Resolved at boot; a runtime change"
            " applies on the next pipeline rebuild (provider re-init)."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
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
