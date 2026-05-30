"""Demo namespace setting definitions.

A single setting for the synthetic demo feature. It exists to prove a feature
declares its own settings namespace through the substrate (the
namespace-completeness gate requires a definitions file per namespace); the
demo service does not read it.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.DEMO,
        key="greeting",
        type=SettingType.STRING,
        default="hello from the demo feature",
        description=(
            "The greeting the demo feature advertises. Present only to give"
            " the demo namespace a registered setting; the demo service uses"
            " a boot constant rather than reading this value."
        ),
        group="Demo",
        level=SettingLevel.ADVANCED,
    )
)
