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
