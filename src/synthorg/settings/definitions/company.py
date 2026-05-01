"""Company namespace setting definitions."""

from synthorg.core.enums import AutonomyLevel
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_DEFAULT_DRIFT
from synthorg.security.autonomy.models import AutonomyConfig
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

logger = get_logger(__name__)

_r = get_registry()

# Safety net: the SettingDefinition default below and
# :attr:`AutonomyConfig.level`'s default must agree, otherwise a fresh
# install resolves autonomy differently depending on which side read the
# value first.  Raised explicitly (not ``assert``) so ``python -O`` still
# trips the drift guard.
_AUTONOMY_DEFAULT_RAW = AutonomyConfig.model_fields["level"].default
if not isinstance(_AUTONOMY_DEFAULT_RAW, AutonomyLevel):
    _msg = "AutonomyConfig.level default must be an AutonomyLevel enum"
    logger.error(
        SETTINGS_DEFAULT_DRIFT,
        field="AutonomyConfig.level",
        observed_type=type(_AUTONOMY_DEFAULT_RAW).__name__,
        expected_type="AutonomyLevel",
    )
    raise TypeError(_msg)
_EXPECTED_AUTONOMY_DEFAULT = _AUTONOMY_DEFAULT_RAW.value

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMPANY,
        key="company_name",
        type=SettingType.STRING,
        default=None,
        description="Company display name",
        group="General",
        yaml_path="company_name",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMPANY,
        key="description",
        type=SettingType.STRING,
        default=None,
        description="Company description",
        group="General",
        yaml_path="description",
    )
)

if _EXPECTED_AUTONOMY_DEFAULT != "supervised":
    _msg = (
        "AutonomyConfig.level default drifted from the 'autonomy_level' "
        "SettingDefinition default; update both in lockstep."
    )
    logger.error(
        SETTINGS_DEFAULT_DRIFT,
        field="AutonomyConfig.level",
        expected="supervised",
        observed=_EXPECTED_AUTONOMY_DEFAULT,
    )
    raise RuntimeError(_msg)
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMPANY,
        key="autonomy_level",
        type=SettingType.ENUM,
        default=_EXPECTED_AUTONOMY_DEFAULT,
        description=(
            "Default company-wide autonomy level. Fresh installs ship with"
            " 'supervised' so most state-mutating agent actions queue for"
            " approval before execution; raise to 'semi' or 'full' once"
            " operators trust the organization. Rank: full > semi >"
            " supervised > locked."
        ),
        group="General",
        enum_values=tuple(level.value for level in AutonomyLevel),
        yaml_path="config.autonomy.level",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMPANY,
        key="name_locales",
        type=SettingType.JSON,
        default='["__all__"]',
        description=(
            "Faker locales for agent name generation. "
            'Use ["__all__"] for all Latin-script locales or a list of '
            'locale codes (e.g. ["en_US", "fr_FR", "de_DE"]).'
        ),
        group="Names",
        yaml_path="name_locales",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMPANY,
        key="agents",
        type=SettingType.JSON,
        default=None,
        description="Agent configurations (JSON array of AgentConfig objects)",
        group="Structure",
        yaml_path="agents",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.COMPANY,
        key="departments",
        type=SettingType.JSON,
        default=None,
        description="Department hierarchy (JSON array of Department objects)",
        group="Structure",
        yaml_path="departments",
    )
)
