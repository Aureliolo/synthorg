"""Notification subsystem event constants."""

from typing import Final

NOTIFICATION_DISPATCHED: Final[str] = "notification.dispatched"
NOTIFICATION_DISPATCH_FAILED: Final[str] = "notification.dispatch.failed"
NOTIFICATION_SINK_REGISTERED: Final[str] = "notification.sink.registered"
NOTIFICATION_CONSOLE_DELIVERED: Final[str] = "notification.console.delivered"
NOTIFICATION_NTFY_DELIVERED: Final[str] = "notification.ntfy.delivered"
NOTIFICATION_NTFY_FAILED: Final[str] = "notification.ntfy.failed"
NOTIFICATION_SLACK_DELIVERED: Final[str] = "notification.slack.delivered"
NOTIFICATION_SLACK_FAILED: Final[str] = "notification.slack.failed"
NOTIFICATION_EMAIL_DELIVERED: Final[str] = "notification.email.delivered"
NOTIFICATION_EMAIL_FAILED: Final[str] = "notification.email.failed"
NOTIFICATION_FILTERED: Final[str] = "notification.filtered"
NOTIFICATION_SINK_CONFIG_INVALID: Final[str] = "notification.sink.config_invalid"
NOTIFICATION_SINK_UNKNOWN_TYPE: Final[str] = "notification.sink.unknown_type"
NOTIFICATION_SINK_DISABLED: Final[str] = "notification.sink.disabled"
NOTIFICATION_NO_SINKS: Final[str] = "notification.no_sinks"
NOTIFICATION_SINK_START_FAILED: Final[str] = "notification.sink.start_failed"
NOTIFICATION_SINK_CLOSE_FAILED: Final[str] = "notification.sink.close_failed"
NOTIFICATION_DISPATCHER_STARTED: Final[str] = "notification.dispatcher.started"
NOTIFICATION_DISPATCHER_CLOSED: Final[str] = "notification.dispatcher.closed"
NOTIFICATION_DISPATCHER_PAUSED: Final[str] = "notification.dispatcher.paused"
NOTIFICATION_DISPATCHER_RESOLVE_FAILED: Final[str] = (
    "notification.dispatcher.resolve_failed"
)
NOTIFICATION_EMAIL_PARTIAL_CREDENTIALS: Final[str] = (
    "notification.email.partial_credentials"
)

# -- Background notification tasks (fire-and-forget tracked sends) ----------

NOTIFICATION_BUDGET_EXHAUSTED_SEND: Final[str] = "notification.budget_exhausted.send"
NOTIFICATION_ESCALATION_SEND: Final[str] = "notification.escalation.send"
NOTIFICATION_SEND_FAILED: Final[str] = "notification.send.failed"
NOTIFICATION_SINK_DEFAULT_FALLBACK: Final[str] = "notification.sink.default_fallback"
