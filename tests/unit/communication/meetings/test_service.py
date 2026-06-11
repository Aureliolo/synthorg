"""Unit tests for :class:`MeetingService`.

Focus on the rewritten ``delete_meeting`` path: routes through the
orchestrator's in-memory record store, returns the deletion bool
unchanged, and emits the audit-grade
``COMMUNICATION_MEETING_DELETED`` event on success only.
"""

from unittest.mock import Mock

import pytest
import structlog.testing

from synthorg.communication.meeting.models import MeetingRecord
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meetings.service import MeetingService
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.communication import (
    COMMUNICATION_MEETING_DELETED,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _make_service(*, deleted: bool) -> tuple[MeetingService, Mock]:
    orch = mock_of[MeetingOrchestrator]()
    orch.delete_record.return_value = deleted
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


class TestMeetingServiceGetMeeting:
    """``get_meeting`` is an O(1) delegate, not a full-record scan."""

    async def test_delegates_to_get_record_and_never_scans(self) -> None:
        sentinel = mock_of[MeetingRecord]()
        orch = mock_of[MeetingOrchestrator]()
        orch.get_record.return_value = sentinel
        service = MeetingService(orchestrator=orch)

        result = await service.get_meeting(NotBlankStr("meet-1"))

        assert result is sentinel
        orch.get_record.assert_called_once_with("meet-1")
        orch.get_records.assert_not_called()

    async def test_returns_none_when_record_absent(self) -> None:
        orch = mock_of[MeetingOrchestrator]()
        orch.get_record.return_value = None
        service = MeetingService(orchestrator=orch)

        assert await service.get_meeting(NotBlankStr("nope")) is None
