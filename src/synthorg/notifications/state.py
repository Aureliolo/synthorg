"""Notifications feature state slice.

Holds the notification dispatcher (severity-filtered fan-out to the
console / ntfy / Slack / email sinks). Read only by the lifecycle layer
(startup apply + shutdown aclose), not by request handlers.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.notifications.dispatcher import (
    NotificationDispatcher,  # noqa: TC001
)


class NotificationsStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the notifications feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dispatcher: NotificationDispatcher | None = None
