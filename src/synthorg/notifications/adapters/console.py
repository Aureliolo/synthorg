"""Console notification sink -- logs to structured logging."""

from synthorg.notifications.models import Notification
from synthorg.observability import get_logger
from synthorg.observability.events.notification import (
    NOTIFICATION_CONSOLE_DELIVERED,
)

logger = get_logger(__name__)


class ConsoleNotificationSink:
    """Notification sink that logs to structured logging.

    Default sink -- no external dependencies required.
    """

    @property
    def sink_name(self) -> str:
        """Return the sink identifier."""
        return "console"

    async def send(self, notification: Notification) -> None:
        """Log the notification via structured logging.

        Args:
            notification: The notification to log.
        """
        logger.info(
            NOTIFICATION_CONSOLE_DELIVERED,
            notification_id=str(notification.id),
            category=notification.category,
            severity=notification.severity,
            title=notification.title,
            source=notification.source,
        )

    async def start(self) -> None:
        """No-op (stateless sink); satisfies the protocol."""

    async def close(self) -> None:
        """No-op (stateless sink); satisfies the protocol."""
