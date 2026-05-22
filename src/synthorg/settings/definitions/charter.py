"""Charter namespace setting definitions.

Covers the deep CEO interview to project charter subsystem. The values
are sourced from ``RootConfig.meta.charter`` at startup (Cat-2 config:
env > code default); these entries exist for ``/settings``
discoverability and are baked in at process startup.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_BOOTSTRAP_NOTE = (
    "[Bootstrap-only -- read via RootConfig.meta.charter at startup; this"
    " entry exists for /settings discoverability only.] "
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            _BOOTSTRAP_NOTE
            + "Enable the deep CEO interview to project charter interface"
            " (/meta/charters)."
        ),
        group="Charter",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_model",
        type=SettingType.STRING,
        default="example-small-001",
        description=_BOOTSTRAP_NOTE + "Model identifier for charter-interview turns.",
        group="Charter",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_max_turns",
        type=SettingType.INTEGER,
        default="12",
        description=(
            _BOOTSTRAP_NOTE
            + "Maximum elicitation turns before the interview force-closes"
            " without converging on a charter."
        ),
        group="Charter",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_temperature",
        type=SettingType.FLOAT,
        default="0.3",
        description=(
            _BOOTSTRAP_NOTE + "Sampling temperature for charter-interview turns."
        ),
        group="Charter",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="interview_max_tokens",
        type=SettingType.INTEGER,
        default="3000",
        description=_BOOTSTRAP_NOTE + "Token budget for one charter-interview turn.",
        group="Charter",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CHARTER,
        key="default_currency",
        type=SettingType.STRING,
        default="USD",  # lint-allow: regional-defaults -- budget DEFAULT_CURRENCY
        description=(
            _BOOTSTRAP_NOTE
            + "ISO 4217 currency assumed for the charter budget envelope when"
            " the interview does not elicit one; must match budget.currency"
            " for charter approval to create the backing forecast."
        ),
        group="Charter",
        level=SettingLevel.ADVANCED,
        read_only_post_init=True,
        restart_required=True,
    )
)
