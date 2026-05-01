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
        yaml_path="coordination.max_concurrency_per_wave",
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
        yaml_path="coordination.fail_fast",
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
        yaml_path="coordination.enable_workspace_isolation",
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
        yaml_path="coordination.base_branch",
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
        yaml_path="coordination.department_policy_cas_retry_attempts",
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
        yaml_path="workflow.sprint.ceremony_policy.strategy",
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
        yaml_path="workflow.sprint.ceremony_policy.strategy_config",
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
        yaml_path="workflow.sprint.ceremony_policy.velocity_calculator",
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
        yaml_path="workflow.sprint.ceremony_policy.auto_transition",
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
        yaml_path="workflow.sprint.ceremony_policy.transition_threshold",
    )
)

# The next two settings are aggregate JSON blobs managed entirely through the
# settings service (keyed by department or ceremony name).  They intentionally
# omit yaml_path because they do not map to a single YAML config path -- the
# YAML company config stores per-department ceremony_policy inline on each
# department object, not as a separate top-level blob.
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
