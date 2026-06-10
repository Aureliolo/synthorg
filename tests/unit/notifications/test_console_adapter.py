"""Tests for the console notification sink."""

import pytest
import structlog.testing

from synthorg.notifications.adapters.console import ConsoleNotificationSink
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability.events.notification import (
    NOTIFICATION_CONSOLE_DELIVERED,
)


@pytest.mark.unit
class TestConsoleNotificationSink:
    def test_sink_name(self) -> None:
        sink = ConsoleNotificationSink()
        assert sink.sink_name == "console"

    async def test_send_emits_delivered_event(self) -> None:
        sink = ConsoleNotificationSink()
        n = Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.ERROR,
            title="Test error",
            source="test",
        )
        with structlog.testing.capture_logs() as logs:
            await sink.send(n)

        delivered = [e for e in logs if e["event"] == NOTIFICATION_CONSOLE_DELIVERED]
        assert len(delivered) == 1
        assert delivered[0]["notification_id"] == str(n.id)
