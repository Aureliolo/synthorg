"""Tests for meeting controller."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
    MeetingStatus,
)
from synthorg.communication.meeting.models import (
    MeetingAgenda,
    MeetingContribution,
    MeetingMinutes,
    MeetingRecord,
)
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator

# Re-use the shared conftest helpers.
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)


def _create_meeting_test_app(
    *,
    meeting_orchestrator: MagicMock,
    meeting_scheduler: MagicMock | None,
) -> Any:
    """Build a Litestar test app with the given meeting services."""
    from synthorg.api.approval_store import ApprovalStore
    from synthorg.api.auth.config import AuthConfig
    from synthorg.api.auth.service import AuthService
    from synthorg.budget.tracker import CostTracker
    from synthorg.config.schema import RootConfig

    persistence = FakePersistenceBackend()
    bus = FakeMessageBus()
    auth_service = AuthService(
        AuthConfig(
            jwt_secret="test-secret-that-is-at-least-32-characters-long",
        ),
    )

    from tests.unit.api.conftest import _seed_test_users

    _seed_test_users(persistence, auth_service)

    return create_app(
        config=RootConfig(company_name="test-company"),
        persistence=persistence,
        message_bus=bus,
        cost_tracker=CostTracker(),
        approval_store=ApprovalStore(),
        auth_service=auth_service,
        meeting_orchestrator=meeting_orchestrator,
        meeting_scheduler=meeting_scheduler,
    )


def _make_minutes(
    meeting_id: str = "mtg-abc123",
    *,
    with_contributions: bool = False,
) -> MeetingMinutes:
    """Create minimal valid MeetingMinutes."""
    now = datetime.now(UTC)
    contributions: tuple[MeetingContribution, ...] = ()
    total_input = 0
    total_output = 0

    if with_contributions:
        contributions = (
            MeetingContribution(
                agent_id="agent-alpha",
                content="First point",
                phase=MeetingPhase.ROUND_ROBIN_TURN,
                turn_number=0,
                input_tokens=100,
                output_tokens=200,
                timestamp=now,
            ),
            MeetingContribution(
                agent_id="agent-beta",
                content="Second point",
                phase=MeetingPhase.ROUND_ROBIN_TURN,
                turn_number=1,
                input_tokens=150,
                output_tokens=250,
                timestamp=now,
            ),
            MeetingContribution(
                agent_id="agent-alpha",
                content="Follow-up",
                phase=MeetingPhase.DISCUSSION,
                turn_number=2,
                input_tokens=50,
                output_tokens=100,
                timestamp=now,
            ),
        )
        total_input = sum(c.input_tokens for c in contributions)
        total_output = sum(c.output_tokens for c in contributions)

    participant_ids = (
        ("agent-alpha", "agent-beta") if with_contributions else ("participant-1",)
    )

    started = now - timedelta(seconds=120)
    return MeetingMinutes(
        meeting_id=meeting_id,
        protocol_type=MeetingProtocolType.ROUND_ROBIN,
        leader_id="leader-id",
        participant_ids=participant_ids,
        agenda=MeetingAgenda(title="Test"),
        contributions=contributions,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        started_at=started,
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
def mock_orchestrator() -> MagicMock:
    """Mock orchestrator with pre-loaded records."""
    orch = MagicMock(spec=MeetingOrchestrator)
    records = (
        _make_record("mtg-001", "standup", MeetingStatus.COMPLETED),
        _make_record("mtg-002", "retro", MeetingStatus.FAILED),
    )
    orch.get_records = MagicMock(return_value=records)
    return orch


@pytest.fixture
def mock_orchestrator_with_contributions() -> MagicMock:
    """Mock orchestrator with records that include contributions."""
    orch = MagicMock(spec=MeetingOrchestrator)
    completed = MeetingRecord(
        meeting_id="mtg-rich",
        meeting_type_name="standup",
        protocol_type=MeetingProtocolType.ROUND_ROBIN,
        status=MeetingStatus.COMPLETED,
        token_budget=5000,
        minutes=_make_minutes("mtg-rich", with_contributions=True),
    )
    failed = _make_record("mtg-fail", "retro", MeetingStatus.FAILED)
    orch.get_records = MagicMock(return_value=(completed, failed))
    return orch


@pytest.fixture
def mock_scheduler() -> MagicMock:
    """Mock scheduler."""
    # Cannot use spec=MeetingScheduler: PEP 649 deferred annotation
    # for MeetingsConfig in __init__ causes NameError in inspect.
    sched = MagicMock()
    sched.trigger_event = AsyncMock(return_value=())
    sched.start = AsyncMock()
    sched.stop = AsyncMock()
    return sched


@pytest.fixture
def meeting_client(
    mock_orchestrator: MagicMock,
    mock_scheduler: MagicMock,
) -> Iterator[TestClient[Any]]:
    """Test client with meeting orchestrator and scheduler configured."""
    app = _create_meeting_test_app(
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
        assert "error_code" in body or "type" in body or "error" in body

    def test_delete_meeting_returns_200_when_record_exists(
        self,
        meeting_client: TestClient[Any],
        mock_orchestrator: MagicMock,
    ) -> None:
        mock_orchestrator.delete_record = MagicMock(return_value=True)

        resp = meeting_client.delete("/api/v1/meetings/mtg-001")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] is None
        mock_orchestrator.delete_record.assert_called_once_with("mtg-001")

    def test_delete_meeting_returns_404_when_id_missing(
        self,
        meeting_client: TestClient[Any],
        mock_orchestrator: MagicMock,
    ) -> None:
        mock_orchestrator.delete_record = MagicMock(return_value=False)

        resp = meeting_client.delete("/api/v1/meetings/mtg-unknown")

        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False

    def test_trigger_endpoint_calls_mock_scheduler(
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
        mock_scheduler.trigger_event.assert_awaited_once_with(
            "deploy_complete",
            context={},
        )

    def test_trigger_returns_503_when_scheduler_not_configured(
        self,
        mock_orchestrator: MagicMock,
        mock_scheduler: MagicMock,
    ) -> None:
        """Degraded mode (``meeting_scheduler=None``) yields 503, not 500.

        When the meeting agent caller is unconfigured,
        ``auto_wire_meetings`` leaves ``meeting_scheduler`` as ``None``
        and ``app_state.meeting_scheduler`` raises
        ``ServiceUnavailableError`` on access.  Simulating the
        post-wire state here (by clearing the stored scheduler after
        construction) verifies that the controller returns a clean
        503 instead of the PR regressing to an ``AttributeError``
        that would surface as a 500.
        """
        app = _create_meeting_test_app(
            meeting_orchestrator=mock_orchestrator,
            meeting_scheduler=mock_scheduler,
        )
        app.state.app_state._meeting_scheduler = None
        with TestClient(app) as client:
            client.headers.update(make_auth_headers("ceo"))
            resp = client.post(
                "/api/v1/meetings/trigger",
                json={"event_name": "deploy_complete"},
            )
            assert resp.status_code == 503
            body = resp.json()
            assert body["success"] is False

    def test_trigger_response_includes_analytics(
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
        item = resp.json()["data"][0]
        # _make_record with default args produces a COMPLETED record
        # with empty-contributions minutes (120s duration).
        assert item["token_usage_by_participant"] == {}
        assert item["contribution_rank"] == []
        assert item["meeting_duration_seconds"] == 120.0

    def test_trigger_rejects_context_exceeding_settings_cap(
        self,
        meeting_client: TestClient[Any],
        mock_scheduler: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Endpoint enforces ``api.max_meeting_context_keys`` from settings.

        Patches ``_resolve_max_context_keys`` to return 1 so the test
        does not need a fully wired settings backend, then submits a
        context with 2 keys and asserts the controller short-circuits
        with a validation error before calling the scheduler.
        """
        from synthorg.api.controllers import meetings as _meetings_module

        async def _capped_at_one(_app_state: object) -> int:
            return 1

        monkeypatch.setattr(
            _meetings_module,
            "_resolve_max_context_keys",
            _capped_at_one,
        )
        resp = meeting_client.post(
            "/api/v1/meetings/trigger",
            json={
                "event_name": "deploy_complete",
                "context": {"k1": "v1", "k2": "v2"},
            },
        )
        # ValidationError -> structured 4xx envelope (RFC 9457).
        assert resp.status_code in {400, 422}, resp.text
        body = resp.json()
        assert body["success"] is False
        # Scheduler must not be called when validation fails before
        # dispatch -- otherwise the cap is decorative.
        mock_scheduler.trigger_event.assert_not_awaited()

    def test_trigger_accepts_context_at_settings_cap(
        self,
        meeting_client: TestClient[Any],
        mock_scheduler: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exact-cap context passes validation and reaches the scheduler."""
        from synthorg.api.controllers import meetings as _meetings_module

        async def _capped_at_two(_app_state: object) -> int:
            return 2

        monkeypatch.setattr(
            _meetings_module,
            "_resolve_max_context_keys",
            _capped_at_two,
        )
        record = _make_record("mtg-cap-ok")
        mock_scheduler.trigger_event.return_value = (record,)

        resp = meeting_client.post(
            "/api/v1/meetings/trigger",
            json={
                "event_name": "deploy_complete",
                "context": {"k1": "v1", "k2": "v2"},
            },
        )
        assert resp.status_code == 200
        mock_scheduler.trigger_event.assert_awaited_once()

    def test_oversized_meeting_id_rejected(
        self,
        meeting_client: TestClient[Any],
    ) -> None:
        long_id = "x" * 129
        resp = meeting_client.get(f"/api/v1/meetings/{long_id}")
        assert resp.status_code == 400

    def test_oversized_meeting_type_query_rejected(
        self,
        meeting_client: TestClient[Any],
    ) -> None:
        long_type = "x" * 129
        resp = meeting_client.get(
            "/api/v1/meetings",
            params={"meeting_type": long_type},
        )
        assert resp.status_code == 422

    def test_auto_wired_meetings_returns_200(
        self,
    ) -> None:
        """Auto-wired meeting services should return 200 (empty list)."""
        app = _create_app_without_explicit_meetings()
        with TestClient(app) as client:
            client.headers.update(make_auth_headers("ceo"))
            resp = client.get("/api/v1/meetings")
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is True
            assert body["data"] == []


@pytest.fixture
def analytics_client(
    mock_orchestrator_with_contributions: MagicMock,
    mock_scheduler: MagicMock,
) -> Iterator[TestClient[Any]]:
    """Test client with meeting records that include contributions."""
    app = _create_meeting_test_app(
        meeting_orchestrator=mock_orchestrator_with_contributions,
        meeting_scheduler=mock_scheduler,
    )
    with TestClient(app) as client:
        client.headers.update(make_auth_headers("ceo"))
        yield client


@pytest.mark.unit
class TestMeetingAnalytics:
    """Tests for computed meeting analytics fields."""

    def test_list_includes_analytics_fields(
        self,
        analytics_client: TestClient[Any],
    ) -> None:
        resp = analytics_client.get("/api/v1/meetings")
        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert "token_usage_by_participant" in item
        assert "contribution_rank" in item
        assert "meeting_duration_seconds" in item

    def test_token_usage_by_participant(
        self,
        analytics_client: TestClient[Any],
    ) -> None:
        resp = analytics_client.get("/api/v1/meetings/mtg-rich")
        data = resp.json()["data"]
        usage = data["token_usage_by_participant"]
        # agent-alpha: (100+200) + (50+100) = 450
        assert usage["agent-alpha"] == 450
        # agent-beta: (150+250) = 400
        assert usage["agent-beta"] == 400

    def test_contribution_rank_sorted_desc(
        self,
        analytics_client: TestClient[Any],
    ) -> None:
        resp = analytics_client.get("/api/v1/meetings/mtg-rich")
        data = resp.json()["data"]
        rank = data["contribution_rank"]
        assert rank == ["agent-alpha", "agent-beta"]

    def test_meeting_duration_seconds(
        self,
        analytics_client: TestClient[Any],
    ) -> None:
        resp = analytics_client.get("/api/v1/meetings/mtg-rich")
        data = resp.json()["data"]
        assert data["meeting_duration_seconds"] == 120.0

    def test_analytics_empty_for_non_completed(
        self,
        analytics_client: TestClient[Any],
    ) -> None:
        resp = analytics_client.get("/api/v1/meetings/mtg-fail")
        data = resp.json()["data"]
        assert data["token_usage_by_participant"] == {}
        assert data["contribution_rank"] == []
        assert data["meeting_duration_seconds"] is None

    def test_get_meeting_includes_analytics(
        self,
        analytics_client: TestClient[Any],
    ) -> None:
        resp = analytics_client.get("/api/v1/meetings/mtg-rich")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "token_usage_by_participant" in data
        assert "contribution_rank" in data
        assert "meeting_duration_seconds" in data

    def test_completed_meeting_with_empty_contributions(
        self,
        meeting_client: TestClient[Any],
    ) -> None:
        """Completed meeting with minutes but no contributions."""
        resp = meeting_client.get("/api/v1/meetings/mtg-001")
        data = resp.json()["data"]
        assert data["token_usage_by_participant"] == {}
        assert data["contribution_rank"] == []
        # Duration is still computed from started_at/ended_at
        assert data["meeting_duration_seconds"] is not None


@pytest.mark.unit
class TestTriggerMeetingRequestValidation:
    """Tests for TriggerMeetingRequest input validation bounds."""

    def test_valid_request(self) -> None:
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        req = TriggerMeetingRequest(
            event_name="deploy_complete",
            context={"key": "value"},
        )
        assert req.event_name == "deploy_complete"

    def test_rejects_blank_event_name(self) -> None:
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        with pytest.raises(ValueError, match="whitespace"):
            TriggerMeetingRequest(event_name="   ", context={})

    def test_accepts_too_many_context_keys_at_model_layer(self) -> None:
        """The aggregate key cap is enforced controller-side now.

        ``TriggerMeetingRequest`` only validates per-key/per-value
        invariants; ``trigger_meeting`` reads
        ``api.max_meeting_context_keys`` from the settings backend at
        request time and raises :class:`ValidationError` when the
        cap is exceeded.  See ``test_rejects_too_many_context_keys``
        below for the controller-layer assertion.
        """
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        ctx: dict[str, str | list[str]] = {f"key-{i}": "val" for i in range(21)}
        # No raise: the model-level check no longer covers key count.
        request = TriggerMeetingRequest(event_name="evt", context=ctx)
        assert len(request.context) == 21

    def test_rejects_oversized_context_key(self) -> None:
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        with pytest.raises(ValueError, match="key must be at most"):
            TriggerMeetingRequest(
                event_name="evt",
                context={"k" * 257: "val"},
            )

    def test_rejects_oversized_context_value(self) -> None:
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        with pytest.raises(ValueError, match="value must be at most"):
            TriggerMeetingRequest(
                event_name="evt",
                context={"key": "v" * 1025},
            )

    def test_rejects_oversized_context_list_item(self) -> None:
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        with pytest.raises(ValueError, match="list item must be at most"):
            TriggerMeetingRequest(
                event_name="evt",
                context={"key": ["valid", "v" * 1025]},
            )

    def test_rejects_too_many_context_list_items(self) -> None:
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        with pytest.raises(ValueError, match="list must have at most"):
            TriggerMeetingRequest(
                event_name="evt",
                context={"key": [f"v{i}" for i in range(51)]},
            )

    def test_accepts_boundary_context(self) -> None:
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        ctx: dict[str, str | list[str]] = {f"k{i}": "v" for i in range(20)}
        req = TriggerMeetingRequest(event_name="evt", context=ctx)
        assert len(req.context) == 20

    def test_accepts_exact_max_key_and_value_lengths(self) -> None:
        """Exact boundary: key=256 chars, value=1024 chars accepted."""
        from synthorg.api.controllers.meetings import TriggerMeetingRequest

        key = "k" * 256
        value = "v" * 1024
        req = TriggerMeetingRequest(event_name="evt", context={key: value})
        assert len(req.context) == 1
        assert req.context[key] == value


@pytest.mark.unit
class TestResolveMaxContextKeysFallback:
    """``_resolve_max_context_keys`` settings-backend fallback contract.

    The helper falls back to ``_MAX_CONTEXT_KEYS_FALLBACK`` (20) when
    the config resolver is unavailable or raises.  These tests guard
    that contract so a settings backend outage does not break meeting
    triggers.
    """

    async def test_returns_fallback_when_config_resolver_absent(self) -> None:
        from unittest.mock import MagicMock as _MagicMock

        from synthorg.api.controllers.meetings import _resolve_max_context_keys

        app_state = _MagicMock()
        app_state.has_config_resolver = False
        result = await _resolve_max_context_keys(app_state)
        assert result == 20  # _MAX_CONTEXT_KEYS_FALLBACK

    async def test_returns_fallback_on_resolver_exception(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        from synthorg.api.controllers.meetings import _resolve_max_context_keys

        app_state = _MagicMock()
        app_state.has_config_resolver = True
        app_state.config_resolver.get_int = _AsyncMock(
            side_effect=RuntimeError("settings backend down"),
        )
        result = await _resolve_max_context_keys(app_state)
        assert result == 20

    async def test_propagates_cancelled_error(self) -> None:
        import asyncio as _asyncio
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        from synthorg.api.controllers.meetings import _resolve_max_context_keys

        app_state = _MagicMock()
        app_state.has_config_resolver = True
        app_state.config_resolver.get_int = _AsyncMock(
            side_effect=_asyncio.CancelledError(),
        )
        with pytest.raises(_asyncio.CancelledError):
            await _resolve_max_context_keys(app_state)

    async def test_returns_resolved_value_on_success(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        from synthorg.api.controllers.meetings import _resolve_max_context_keys

        app_state = _MagicMock()
        app_state.has_config_resolver = True
        app_state.config_resolver.get_int = _AsyncMock(return_value=50)
        result = await _resolve_max_context_keys(app_state)
        assert result == 50

    async def test_negative_resolved_value_falls_back(self) -> None:
        """Negative caps are nonsensical; fall back to the registry default."""
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        from synthorg.api.controllers.meetings import _resolve_max_context_keys

        app_state = _MagicMock()
        app_state.has_config_resolver = True
        app_state.config_resolver.get_int = _AsyncMock(return_value=-5)
        result = await _resolve_max_context_keys(app_state)
        assert result == 20  # _MAX_CONTEXT_KEYS_FALLBACK

    async def test_recovery_log_after_fallback(self) -> None:
        """A failure-then-success sequence emits the recovery signal."""
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        import structlog.testing

        from synthorg.api.controllers.meetings import _resolve_max_context_keys
        from synthorg.observability.events.api import API_SETTINGS_BACKEND_RECOVERED

        app_state = _MagicMock()
        app_state.has_config_resolver = True
        app_state.config_resolver.get_int = _AsyncMock(
            side_effect=[RuntimeError("settings backend down"), 30],
        )
        # First call: raises -> fallback (arms the recovery flag).
        await _resolve_max_context_keys(app_state)
        # Second call: succeeds -> emits recovery event.
        with structlog.testing.capture_logs() as cap:
            result = await _resolve_max_context_keys(app_state)
        events = [e["event"] for e in cap]
        assert API_SETTINGS_BACKEND_RECOVERED in events
        assert result == 30


def _create_app_without_explicit_meetings() -> Any:
    """Create app without explicit meeting services (auto-wired)."""
    from synthorg.api.approval_store import ApprovalStore
    from synthorg.api.auth.config import AuthConfig
    from synthorg.api.auth.service import AuthService
    from synthorg.budget.tracker import CostTracker
    from synthorg.config.schema import RootConfig

    persistence = FakePersistenceBackend()
    bus = FakeMessageBus()
    auth_service = AuthService(
        AuthConfig(
            jwt_secret="test-secret-that-is-at-least-32-characters-long",
        ),
    )

    from tests.unit.api.conftest import _seed_test_users

    _seed_test_users(persistence, auth_service)

    return create_app(
        config=RootConfig(company_name="test"),
        persistence=persistence,
        message_bus=bus,
        cost_tracker=CostTracker(),
        approval_store=ApprovalStore(),
        auth_service=auth_service,
        # No meeting_orchestrator or meeting_scheduler -- auto-wired.
    )
