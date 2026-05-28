# module-kind: feature
"""Backup/restore feature manifest.

Declares the backup feature's surface: its settings namespace, state slice,
and REST controller. The backup service is built directly at boot, so the
feature has no MCP domain or ghost-wired symbols.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.backup import BackupController
from synthorg.backup.state import BackupStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="backup",
    settings_namespace=SettingNamespace.BACKUP,
    state_slice=BackupStateSlice,
    controllers=(BackupController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
