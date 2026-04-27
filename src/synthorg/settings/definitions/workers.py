"""Workers namespace setting definitions.

Single read-only-post-init entry for the uvicorn worker count.  Read
once at process start; the registry entry exists for /settings UI
discoverability so operators can introspect the current value through
the standard API surface, but mutation through ``SettingsService.set``
is rejected (the operator must update the env var or YAML and restart
the process).
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.WORKERS,
        key="count",
        type=SettingType.INTEGER,
        default="1",
        description=(
            "Uvicorn worker process count.  Sourced from the"
            " SYNTHORG_WORKERS env var > YAML (server.workers) >"
            " default at process start.  Read-only post-init: a"
            " runtime change requires a process restart."
        ),
        group="Workers",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        env_var_override="SYNTHORG_WORKERS",
        min_value=1,
        max_value=64,
        yaml_path="server.workers",
    )
)
