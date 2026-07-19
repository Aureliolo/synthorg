"""Notifications namespace setting definitions.

Covers HTTP and SMTP client timeouts for the Slack, ntfy, and email
notification sink adapters, plus the dashboard's notification-routing
preferences (backend source of truth; the web client persists nothing).
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="preferences",
        type=SettingType.JSON,
        default='{"routeOverrides": {}, "globalMute": false}',
        description=(
            "Dashboard notification-routing preferences (per-category route "
            "overrides and the global mute flag) as a JSON object. The browser "
            "Notification permission is per-device and is NOT stored here."
        ),
        group="Dashboard",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="slack_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description=(
            "HTTP timeout for Slack Web API posts (chat.postMessage). A"
            " change rebuilds the dispatcher's sinks without a restart."
        ),
        group="Slack",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=60.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="ntfy_webhook_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description=(
            "HTTP timeout for ntfy.sh webhook posts. A change rebuilds"
            " the dispatcher's sinks without a restart."
        ),
        group="ntfy",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=60.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="email_smtp_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description=(
            "Socket timeout for SMTP connections when sending email."
            " A change rebuilds the dispatcher's sinks without a restart."
        ),
        group="Email",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=60.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="ntfy_default_url",
        type=SettingType.STRING,
        default="",
        description=(
            "Default ntfy server URL when a notification sink does not"
            " specify one explicitly. Empty by default: an operator must"
            " set an explicit endpoint (a self-hosted ntfy avoids leaking"
            " topic names to the public ntfy.sh instance). HTTPS only."
            " A change rebuilds the dispatcher's sinks without a restart."
        ),
        group="ntfy",
        level=SettingLevel.ADVANCED,
        # Empty (unset) or an explicit https endpoint; http:// is rejected
        # so topic names are never sent in plaintext.
        validator_pattern=r"^(|https://[\w.\-:]+(?:/.*)?)$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="dispatcher_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master kill switch for the notification dispatcher. When"
            " False every ``dispatch()`` call short-circuits before"
            " touching any sink -- pauses outbound notifications without"
            " tearing down sinks or lifecycle. Resolver outage falls"
            " back to enabled (operators silence by setting the value"
            " explicitly, never by inducing a settings outage)."
        ),
        group="Dispatcher",
        level=SettingLevel.ADVANCED,
    )
)
