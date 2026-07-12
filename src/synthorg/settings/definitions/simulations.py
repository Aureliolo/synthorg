"""Simulations namespace setting definitions.

Covers per-run timeout knobs for the synthetic-client simulation
runner.  See ``src/synthorg/api/controllers/simulations.py`` for the
consumers.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="task_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Maximum wall-clock time a synthetic-client simulated task may run"
            " before timeout."
        ),
        group="Timeouts",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=3600.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="client_intake_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether the synthetic-client intake door is a live work-entry"
            " path. Off by default: the client-request door"
            " (``POST /requests/{id}/approve``) role-plays external customers"
            " and is a benchmark surface, not a standing production front"
            " door, so the real intake entry adapter is wired only when this"
            " is on. SimulationsSettingsSubscriber re-wires the intake entry"
            " adapter on a change, so it applies without a restart; the"
            " ``/objectives`` door is the always-on operator work-entry path."
        ),
        group="Intake",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="intake_strategy",
        type=SettingType.ENUM,
        default="direct",
        enum_values=("direct", "agent"),
        description=(
            "Intake strategy wired into the client-simulation runtime."
            " 'direct' creates a task per accepted request with no LLM call;"
            " 'agent' routes each request through an LLM triage step using the"
            " registered completion provider. A change rebuilds and swaps the"
            " simulation runtime live with no restart."
        ),
        group="Intake",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="intake_model",
        type=SettingType.MODEL_REF,
        default=None,
        description=(
            "Provider + model for the agent intake strategy, selected through"
            " the model picker (a `{provider, model_id}` reference). Only"
            " consulted when simulations.intake_strategy is 'agent'; ignored"
            " by the 'direct' strategy. A change rebuilds and swaps the"
            " simulation runtime live with no restart."
        ),
        group="Intake",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="intake_default_project",
        type=SettingType.STRING,
        default="client-intake",
        description=(
            "Project that the real work-entry path files intake tasks"
            " into. The same value is wired into the intake strategy"
            " (DirectIntake / AgentIntake) and the WorkItem the intake"
            " entry adapter feeds the pipeline, and the project is"
            " created if absent. A change rebuilds and re-wires the simulation"
            " runtime live with no restart, re-resolving the project from the"
            " settings DB when the intake entry adapter is swapped."
        ),
        group="Intake",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="review_pipeline_strategy",
        type=SettingType.ENUM,
        default="internal_only",
        enum_values=("internal_only", "client_then_internal"),
        description=(
            "Review pipeline wired into the client-simulation runtime."
            " 'internal_only' runs a single internal review stage;"
            " 'client_then_internal' prepends a client-delegated stage"
            " when a client pool is available, otherwise it degrades to"
            " internal-only. A change rebuilds and swaps the simulation"
            " runtime live with no restart."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="verification_review_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether the rubric-grading verification stage runs first in"
            " the review pipeline. The stage decomposes a task's"
            " acceptance criteria into atomic probes and grades the work"
            " against a calibrated rubric with a separate evaluator"
            " identity. SimulationsSettingsSubscriber rebuilds the review"
            " pipeline on a change, so it applies without a restart."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="verification_grader",
        type=SettingType.ENUM,
        default="heuristic",
        enum_values=("heuristic", "llm"),
        description=(
            "Grader variant for the verification review stage."
            " 'heuristic' is deterministic and needs no provider;"
            " 'llm' grades via the registered completion provider and"
            " degrades to 'heuristic' when no provider is present."
            " SimulationsSettingsSubscriber rebuilds the review pipeline"
            " on a change, so it applies without a restart."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="verification_decomposer",
        type=SettingType.ENUM,
        default="identity",
        enum_values=("identity", "llm"),
        description=(
            "Decomposer variant for the verification review stage."
            " 'identity' maps each acceptance criterion to one probe"
            " with no LLM call; 'llm' decomposes via the registered"
            " completion provider and degrades to 'identity' when no"
            " provider is present. SimulationsSettingsSubscriber rebuilds"
            " the review pipeline on a change, so it applies without a"
            " restart."
        ),
        group="Review",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="review_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Maximum wall-clock time a synthetic-client simulated code review"
            " may run before timeout."
        ),
        group="Timeouts",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=3600.0,
    )
)
