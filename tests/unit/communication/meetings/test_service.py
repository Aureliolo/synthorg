"""Unit tests for :class:`MeetingService`.

Focus on the rewritten ``delete_meeting`` path: routes through the
orchestrator's in-memory record store, returns the deletion bool
unchanged, and emits the audit-grade
``COMMUNICATION_MEETING_DELETED`` event on success only.
"""

from unittest.mock import MagicMock

import pytest
import structlog.testing

from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meetings.service import MeetingService
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.communication import (
    COMMUNICATION_MEETING_DELETED,
)

pytestmark = pytest.mark.unit


def _make_service(*, deleted: bool) -> tuple[MeetingService, MagicMock]:
    orch = MagicMock(spec=MeetingOrchestrator)
    orch.delete_record = MagicMock(return_value=deleted)
    service = MeetingService(orchestrator=orch)
    return service, orch


class TestMeetingServiceDelete:
    """``MeetingService.delete_meeting`` end-to-end behavior."""

    async def test_returns_true_and_emits_audit_on_success(self) -> None:
        service, orch = _make_service(deleted=True)

        with structlog.testing.capture_logs() as events:
            result = await service.delete_meeting(
                meeting_id=NotBlankStr("mtg-1"),
                actor_id=NotBlankStr("user-1"),
                reason=NotBlankStr("operator user-deletion request"),
            )

        assert result is True
        orch.delete_record.assert_called_once_with("mtg-1")
        audit = [e for e in events if e.get("event") == COMMUNICATION_MEETING_DELETED]
        assert len(audit) == 1
        assert audit[0]["meeting_id"] == "mtg-1"
        assert audit[0]["actor_id"] == "user-1"
        assert audit[0]["reason"] == "operator user-deletion request"

    async def test_returns_false_and_skips_audit_when_id_missing(self) -> None:
        service, orch = _make_service(deleted=False)

        with structlog.testing.capture_logs() as events:
            result = await service.delete_meeting(
                meeting_id=NotBlankStr("missing"),
                actor_id=NotBlankStr("user-1"),
                reason=NotBlankStr("cleanup"),
            )

        assert result is False
        orch.delete_record.assert_called_once_with("missing")
        audit = [e for e in events if e.get("event") == COMMUNICATION_MEETING_DELETED]
        assert audit == []
