"""Tests for the eval-loop remediation action dispatcher."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.pattern_action_dispatcher_impl import (
    RemediationActionDispatcher,
)
from synthorg.notifications.models import Notification, NotificationCategory

pytestmark = pytest.mark.unit


class _RecordingDispatcher:
    """NotificationDispatcher double that records dispatched notifications."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    async def dispatch(self, notification: Notification) -> None:
        self.sent.append(notification)


class TestRemediationActionDispatcher:
    async def test_routes_action_to_notification(self) -> None:
        sink = _RecordingDispatcher()
        dispatcher = RemediationActionDispatcher(notification_dispatcher=sink)

        accepted = await dispatcher.dispatch(
            NotBlankStr("expand_audit_coverage"),
            NotBlankStr("weakness:governance"),
        )

        assert accepted is True
        assert len(sink.sent) == 1
        note = sink.sent[0]
        assert note.category is NotificationCategory.HEALTH
        assert note.metadata["action_id"] == "expand_audit_coverage"
        assert note.metadata["pattern"] == "weakness:governance"
        assert "expand_audit_coverage" in note.title
