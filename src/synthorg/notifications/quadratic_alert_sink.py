# module-kind: adapter
"""Adapter bridging the message-bus quadratic enforcer to notifications.

The :class:`~synthorg.communication.bus.quadratic_enforcement.QuadraticEnforcer`
emits a structured observability event on every detection and forwards a
human-readable alert to an optional ``QuadraticAlertSink``.  This adapter
implements that sink over the :class:`NotificationDispatcher`, so an
operator gets a dispatched notification (Slack / e-mail / etc.) when a
coordination crosses the O(n^2) threshold.

Wired in the construction phase: both the message bus and the dispatcher
exist there, so boot late-binds an instance onto the bus's enforcer.
"""

from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)

_SOURCE = "communication.quadratic_enforcement"


class DispatcherQuadraticAlertSink:
    """Forwards quadratic-overhead alerts to the notification dispatcher.

    Args:
        dispatcher: The notification dispatcher to publish through.
    """

    __slots__ = ("_dispatcher",)

    def __init__(self, *, dispatcher: NotificationDispatcher) -> None:
        self._dispatcher = dispatcher

    async def alert(self, *, title: str, body: str) -> None:
        """Dispatch a system-severity warning for a quadratic detection.

        Args:
            title: Short alert title.
            body: Human-readable alert body.
        """
        await self._dispatcher.dispatch(
            Notification(
                category=NotificationCategory.SYSTEM,
                severity=NotificationSeverity.WARNING,
                title=title,
                body=body,
                source=_SOURCE,
            ),
        )
