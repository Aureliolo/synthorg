"""Backup namespace setting definitions."""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BACKUP,
        key="enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Enable automatic backups",
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BACKUP,
        key="retention_days",
        type=SettingType.INTEGER,
        default="30",
        description="Number of days to retain backups",
        group="Schedule",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=365,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BACKUP,
        key="schedule_hours",
        type=SettingType.INTEGER,
        default="6",
        description="Interval between scheduled backups in hours",
        group="Schedule",
        min_value=1,
        max_value=168,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BACKUP,
        key="path",
        type=SettingType.STRING,
        default="/data/backups",
        description="Directory path for storing backups",
        group="General",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BACKUP,
        key="compression",
        type=SettingType.BOOLEAN,
        default="true",
        description="Compress backups as tar.gz archives",
        group="General",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BACKUP,
        key="on_shutdown",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Create a backup on graceful shutdown. Disabled by default so"
            " enabling backups does not silently add shutdown-triggered"
            " writes; scheduled backups cover the routine guarantee."
        ),
        group="Triggers",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BACKUP,
        key="on_startup",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Create a backup on startup. Disabled by default (CFG-1"
            " audit) -- scheduled backups cover the same guarantee"
            " without surprise writes at boot. Enable for"
            " belt-and-braces coverage in single-instance deployments."
        ),
        group="Triggers",
    )
)
