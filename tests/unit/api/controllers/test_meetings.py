"""Tests for meeting controller."""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.testing import TestClient

from ai_company.api.app import create_app
from ai_company.communication.meeting.enums import (
    MeetingProtocolType,
    MeetingStatus,
)
from ai_company.communication.meeting.models import (
    MeetingAgenda,
    MeetingMinutes,
    MeetingRecord,
)

# Re-use the shared conftest helpers.
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)


def _make_minutes(meeting_id: str = "mtg-abc123") -> MeetingMinutes:
    """Create minimal valid MeetingMinutes."""
    now = datetime.now(UTC)
    return MeetingMinutes(
        meeting_id=meeting_id,
        protocol_type=MeetingProtocolType.ROUND_ROBIN,
        leader_id="leader-id",
        participant_ids=("participant-1",),
        agenda=MeetingAgenda(title="Test"),
        started_at=now,
        ended_at=now,
    )


def _make_record(
    meeting_id: str = "mtg-abc123",
    meeting_type: str = "standup",
    status: MeetingStatus = MeetingStatus.COMPLETED,
    token_budget: int = 2000,
) -> MeetingRecord:
    return MeetingRecord(
        meeting_id=meeting_id,
        meeting_type_name=meeting_type,
        protocol_type=MeetingProtocolType.ROUND_ROBIN,
        status=status,
        token_budget=token_budget,
        minutes=_make_minutes(meeting_id)
        if status == MeetingStatus.COMPLETED
        else None,
        error_message="err" if status == MeetingStatus.FAILED else None,
    )


@pytest.fixture
async def mock_orchestrator() -> MagicMock:
    """Mock orchestrator with pre-loaded records."""
    orch = MagicMock()
    records = (
        _make_record("mtg-001", "standup", MeetingStatus.COMPLETED),
        _make_record("mtg-002", "retro", MeetingStatus.FAILED),
    )
    orch.get_records = MagicMock(return_value=records)
    return orch


@pytest.fixture
def mock_scheduler() -> MagicMock:
    """Mock scheduler."""
    sched = MagicMock()
    sched.trigger_event = AsyncMock(return_value=())
    return sched


@pytest.fixture
def meeting_client(
    mock_orchestrator: MagicMock,
    mock_scheduler: MagicMock,
) -> Generator[TestClient[Any]]:
    """Test client with meeting orchestrator and scheduler configured."""
    from ai_company.api.approval_store import ApprovalStore
    from ai_company.api.auth.service import AuthService
    from ai_company.budget.tracker import CostTracker
    from ai_company.config.schema import RootConfig

    persistence = FakePersistenceBackend()
    bus = FakeMessageBus()
    auth_service = AuthService(
        __import__(
            "ai_company.api.auth.config",
            fromlist=["AuthConfig"],
        ).AuthConfig(jwt_secret="test-secret-that-is-at-least-32-characters-long"),
    )

    # Seed test users so JWT validation succeeds.
    from tests.unit.api.conftest import _seed_test_users

    _seed_test_users(persistence, auth_service)

    app = create_app(
        config=RootConfig(company_name="test-company"),
        persistence=persistence,
        message_bus=bus,
        cost_tracker=CostTracker(),
        approval_store=ApprovalStore(),
        auth_service=auth_service,
        meeting_orchestrator=mock_orchestrator,
        meeting_scheduler=mock_scheduler,
    )
    with TestClient(app) as client:
        client.headers.update(make_auth_headers("ceo"))
        yield client


@pytest.mark.unit
class TestMeetingController:
    """Tests for the meetings controller."""

    def test_list_meetings_returns_records(
        self,
        meeting_client: TestClient[Any],
    ) -> None:
        resp = meeting_client.get("/api/v1/meetings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 2

    def test_list_meetings_with_status_filter(
        self,
        meeting_client: TestClient[Any],
    ) -> None:
        resp = meeting_client.get(
            "/api/v1/meetings",
            params={"status": "completed"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["status"] == "completed"

    def test_list_meetings_with_meeting_type_filter(
        self,
        meeting_client: TestClient[Any],
    ) -> None:
        resp = meeting_client.get(
            "/api/v1/meetings",
            params={"meeting_type": "retro"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["meeting_type_name"] == "retro"

    def test_get_meeting_by_id(
        self,
        meeting_client: TestClient[Any],
    ) -> None:
        resp = meeting_client.get("/api/v1/meetings/mtg-001")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["meeting_id"] == "mtg-001"

    def test_get_unknown_meeting_returns_404(
        self,
        meeting_client: TestClient[Any],
    ) -> None:
        resp = meeting_client.get("/api/v1/meetings/mtg-unknown")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False

    def test_trigger_endpoint_callsmock_scheduler(
        self,
        meeting_client: TestClient[Any],
        mock_scheduler: MagicMock,
    ) -> None:
        record = _make_record("mtg-triggered")
        mock_scheduler.trigger_event.return_value = (record,)

        resp = meeting_client.post(
            "/api/v1/meetings/trigger",
            json={"event_name": "deploy_complete"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    def test_503_when_orchestrator_not_configured(
        self,
    ) -> None:
        """Without meeting_orchestrator, list should 503."""
        from ai_company.api.approval_store import ApprovalStore
        from ai_company.api.auth.service import AuthService
        from ai_company.budget.tracker import CostTracker
        from ai_company.config.schema import RootConfig

        persistence = FakePersistenceBackend()
        bus = FakeMessageBus()
        auth_config = __import__(
            "ai_company.api.auth.config",
            fromlist=["AuthConfig"],
        ).AuthConfig(jwt_secret="test-secret-that-is-at-least-32-characters-long")
        auth_service = AuthService(auth_config)

        from tests.unit.api.conftest import _seed_test_users

        _seed_test_users(persistence, auth_service)

        app = create_app(
            config=RootConfig(company_name="test"),
            persistence=persistence,
            message_bus=bus,
            cost_tracker=CostTracker(),
            approval_store=ApprovalStore(),
            auth_service=auth_service,
            # No meeting_orchestrator or meetingmock_scheduler
        )
        with TestClient(app) as client:
            client.headers.update(make_auth_headers("ceo"))
            resp = client.get("/api/v1/meetings")
            assert resp.status_code == 503
