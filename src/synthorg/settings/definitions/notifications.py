"""Notifications namespace setting definitions.

Covers HTTP and SMTP client timeouts for the Slack, ntfy, and email
notification sink adapters.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="slack_webhook_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description="HTTP timeout for Slack incoming-webhook posts",
        group="Slack",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=60.0,
        yaml_path="notifications.slack.webhook_timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="ntfy_webhook_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description="HTTP timeout for ntfy.sh webhook posts",
        group="ntfy",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=60.0,
        yaml_path="notifications.ntfy.webhook_timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="email_smtp_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description="Socket timeout for SMTP connections when sending email",
        group="Email",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=60.0,
        yaml_path="notifications.email.smtp_timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="slack_default_webhook_url",
        type=SettingType.STRING,
        default="",
        description=(
            "Optional fallback Slack incoming-webhook URL applied when a"
            " Slack notification sink is configured without its own"
            " ``webhook_url`` parameter. Leave blank to require every"
            " sink to specify the URL explicitly."
        ),
        group="Slack",
        level=SettingLevel.ADVANCED,
        sensitive=True,
        restart_required=True,
        validator_pattern=(
            r"^(?:|https://hooks\.slack\.com/services/"
            r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+)$"
        ),
        yaml_path="notifications.slack.default_webhook_url",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.NOTIFICATIONS,
        key="ntfy_default_url",
        type=SettingType.STRING,
        default="https://ntfy.sh",
        description=(
            "Default ntfy server URL when a notification sink does not"
            " specify one explicitly. Override to a self-hosted ntfy"
            " endpoint to avoid leaking topic names to the public"
            " ntfy.sh instance."
        ),
        group="ntfy",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^https?://[\w.\-:]+(?:/.*)?$",
        yaml_path="notifications.ntfy.default_url",
    )
)
