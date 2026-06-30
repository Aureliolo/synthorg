"""Charter namespace setting definitions.

Covers the deep CEO interview to project charter subsystem. The values
are resolved live (DB > env > code default) per interview turn by the
charter interview service, so a ``/settings`` change lands on the next
turn without a restart.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_model",
        type=SettingType.STRING,
        default="example-large-001",
        description="Model identifier for charter-interview turns. This is a"
        " deep, human-in-the-loop elicitation, so it should be a top-tier"
        " reasoning-capable model -- not a small/cheap one.",
        group="Charter",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_max_turns",
        type=SettingType.INTEGER,
        default="12",
        description=(
            "Maximum elicitation turns before the interview force-closes"
            " without converging on a charter."
        ),
        group="Charter",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_temperature",
        type=SettingType.FLOAT,
        default="0.3",
        description="Sampling temperature for charter-interview turns.",
        group="Charter",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_max_tokens",
        type=SettingType.INTEGER,
        default="3000",
        description="Token budget for one charter-interview turn.",
        group="Charter",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="default_currency",
        type=SettingType.STRING,
        default="USD",  # lint-allow: regional-defaults -- budget DEFAULT_CURRENCY
        description=(
            "ISO 4217 currency assumed for the charter budget envelope when"
            " the interview does not elicit one; must match budget.currency"
            " for charter approval to create the backing forecast."
        ),
        group="Charter",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)
