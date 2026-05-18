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
        key="intake_strategy",
        type=SettingType.ENUM,
        default="direct",
        enum_values=("direct", "agent"),
        description=(
            "Intake strategy wired into the client-simulation runtime at"
            " boot. 'direct' creates a task per accepted request with no"
            " LLM call; 'agent' routes each request through an LLM triage"
            " step using the registered completion provider. Baked in at"
            " process startup."
        ),
        group="Intake",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SIMULATIONS,
        key="intake_model",
        type=SettingType.STRING,
        default=None,
        description=(
            "Model identifier passed to the agent intake strategy. Only"
            " consulted when simulations.intake_strategy is 'agent';"
            " ignored by the 'direct' strategy. Baked in at process"
            " startup."
        ),
        group="Intake",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
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
