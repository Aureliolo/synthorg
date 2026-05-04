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

# ── Distributed dispatcher retry tuning ─────────────────────────
# Fallback module constants in workers/dispatcher.py mirror these
# defaults so a dispatcher constructed without bridge-config wiring
# still observes the documented retry budget.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.WORKERS,
        key="dispatcher_publish_max_attempts",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Max publish attempts per task claim before giving up."
            " A transient NATS hiccup should not orphan a task in"
            " ASSIGNED status; retries cap at this many before the"
            " dispatcher emits an exhaustion event."
        ),
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10,
        yaml_path="workers.dispatcher.publish_max_attempts",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.WORKERS,
        key="dispatcher_publish_backoff_base_seconds",
        type=SettingType.FLOAT,
        default="0.1",
        description=(
            "Base delay (seconds) for exponential backoff between"
            " publish retries. Each retry waits ``base * 2**attempt``"
            " bounded by ``dispatcher_publish_backoff_cap_seconds``."
        ),
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
        min_value=0.01,
        max_value=10.0,
        yaml_path="workers.dispatcher.publish_backoff_base_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.WORKERS,
        key="dispatcher_publish_backoff_cap_seconds",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "Upper bound (seconds) on a single inter-attempt delay."
            " Prevents a future bump to ``publish_max_attempts`` from"
            " silently pushing the publish path into multi-second"
            " sleeps."
        ),
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=60.0,
        yaml_path="workers.dispatcher.publish_backoff_cap_seconds",
    )
)
