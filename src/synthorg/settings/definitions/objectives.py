"""Objectives namespace setting definitions.

Covers the high-altitude goal/objective entry adapter. The entry
adapter (``ObjectiveEntryAdapter``) and its boot wiring
(``wire_real_objective_entry``) consume these.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OBJECTIVES,
        key="default_project",
        type=SettingType.STRING,
        default="objectives",
        description=(
            "Project that the real objective work-entry path files"
            " items into. The same value stamps every WorkItem the"
            " objective entry adapter feeds the pipeline, and the"
            " project is created at boot if absent. Baked in at"
            " process startup."
        ),
        group="Objectives",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)
