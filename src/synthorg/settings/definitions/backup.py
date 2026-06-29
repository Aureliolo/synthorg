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
        description=(
            "Directory path for storing backups. A change is pushed onto"
            " the live backup service + retention manager via a settings"
            " subscriber, so it applies without a restart."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
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
        default="true",
        description=(
            "Create a backup on graceful shutdown. On by default so a clean"
            " stop always captures the latest state; scheduled backups cover"
            " the routine guarantee between runs."
        ),
        group="Triggers",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.BACKUP,
        key="on_startup",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Create a backup on startup. On by default for belt-and-braces"
            " coverage: a fresh snapshot before the run begins, alongside the"
            " scheduled backups that cover the routine guarantee."
        ),
        group="Triggers",
    )
)
