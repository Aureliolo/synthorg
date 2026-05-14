"""Settings namespace self-configuration.

The settings dispatcher is the notification pump for all other
setting changes, so it needs configuration for its own poll
behaviour.  These values are read on a best-effort basis after the
settings service has booted -- the dispatcher falls back to
compile-time bootstrap defaults for the first pump cycle.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SETTINGS,
        key="dispatcher_poll_timeout_seconds",
        type=SettingType.FLOAT,
        default="1.0",
        description="Poll timeout for the settings change-notification dispatcher",
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=10.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SETTINGS,
        key="dispatcher_error_backoff_seconds",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "Backoff before retrying after a dispatcher loop iteration raises"
        ),
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=60.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SETTINGS,
        key="dispatcher_max_consecutive_errors",
        type=SettingType.INTEGER,
        default="30",
        description=("Maximum consecutive dispatcher errors before it aborts"),
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
        min_value=5,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SETTINGS,
        key="dispatcher_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Runtime kill switch for the settings change dispatcher poll"
            " loop. When False the loop sleeps the configured poll-timeout"
            " each iteration without consuming the bus or invoking"
            " subscribers; flip back to True to resume without restarting"
            " the dispatcher. Resolver outage falls back to enabled --"
            " operators silence dispatch by setting the value explicitly,"
            " never by inducing a settings outage."
        ),
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SETTINGS,
        key="dispatcher_stop_drain_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description=(
            "Hard deadline for the dispatcher stop() drain. Lifecycle"
            " synchronisation requires services whose stop() drains"
            " across await boundaries to bound the wait so the lifecycle"
            " lock is never held indefinitely if the polling task"
            " ignores cancellation."
        ),
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=60.0,
    )
)
