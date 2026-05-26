"""Tests for org-wide activity feed endpoint."""

from datetime import UTC, datetime, timedelta
from typing import Any, override
from unittest.mock import AsyncMock

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.communication.delegation.models import DelegationRecord
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,
)
from synthorg.config.schema import RootConfig
from synthorg.core.enums import Complexity, TaskType
from synthorg.hr.enums import ActivityEventType, LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.tools.invocation_record import ToolInvocationRecord
from synthorg.tools.invocation_tracker import ToolInvocationTracker
from tests.unit.api.conftest import FakePersistenceBackend, make_auth_headers

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)
_AGENT_ID = "00000000-0000-0000-0000-000000000aaa"


class _FrozenDatetime(datetime):
    """Subclass that makes ``now()`` return the fixed ``_NOW``."""

    @override
    @classmethod
    def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
        return _NOW


@pytest.fixture(autouse=True)
def _freeze_controller_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the activities controller sees a deterministic 'now'."""
    import synthorg.api.controllers.activities as mod

    monkeypatch.setattr(mod, "datetime", _FrozenDatetime)


def _make_lifecycle_event(
    *,
    agent_id: str = _AGENT_ID,
    agent_name: str = "alice",
    event_type: LifecycleEventType = LifecycleEventType.HIRED,
    timestamp: datetime | None = None,
    details: str = "test event",
) -> AgentLifecycleEvent:
    return AgentLifecycleEvent(
        agent_id=agent_id,
        agent_name=agent_name,
        event_type=event_type,
        timestamp=timestamp or _NOW,
        initiated_by="system",
        details=details,
    )


def _make_task_metric(
    *,
    agent_id: str = _AGENT_ID,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    is_success: bool = True,
) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=agent_id,
        task_id="task-001",
        task_type=TaskType.DEVELOPMENT,
        started_at=started_at,
        completed_at=completed_at or _NOW,
        is_success=is_success,
        duration_seconds=10.0,
        cost=0.01,
        currency="EUR",
        turns_used=2,
        tokens_used=150,
        complexity=Complexity.SIMPLE,
    )


async def _make_app_with_broken_tracker(
    fake_persistence: FakePersistenceBackend,
) -> Litestar:
    """Build an app whose performance tracker always raises."""
    from synthorg.api.app import create_app
    from synthorg.api.auth.service import AuthService
    from synthorg.budget.tracker import CostTracker
    from synthorg.settings.registry import get_registry
    from synthorg.settings.service import SettingsService
    from tests.unit.api.conftest import (
        FakeMessageBus,
        _make_test_auth_service,
        _seed_test_users,
    )

    tracker = PerformanceTracker()

    def _raise(**_kwargs: object) -> None:
        msg = "simulated failure"
        raise RuntimeError(msg)

    tracker.get_task_metrics = _raise  # type: ignore[assignment]

    config = RootConfig(company_name="test")
    auth_service: AuthService = _make_test_auth_service()
    bus = FakeMessageBus()
    await bus.start()
    _seed_test_users(fake_persistence, auth_service)
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    return create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
        performance_tracker=tracker,
    )


class TestActivityFeed:
    def test_empty_feed(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    def test_auth_required(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            "/api/v1/activities",
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status_code == 401

    async def test_feed_with_lifecycle_events(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                timestamp=_NOW - timedelta(hours=1),
                event_type=LifecycleEventType.HIRED,
            ),
        )
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                timestamp=_NOW - timedelta(hours=2),
                event_type=LifecycleEventType.ONBOARDED,
            ),
        )
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        # Most recent first
        assert body["data"][0]["event_type"] == "hired"
        assert body["data"][1]["event_type"] == "onboarded"

    async def test_feed_with_task_metrics(
        self,
        test_client: TestClient[Any],
        performance_tracker: PerformanceTracker,
    ) -> None:
        await performance_tracker.record_task_metric(
            _make_task_metric(completed_at=_NOW - timedelta(hours=1)),
        )
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["event_type"] == "task_completed"

    async def test_feed_with_task_started(
        self,
        test_client: TestClient[Any],
        performance_tracker: PerformanceTracker,
    ) -> None:
        await performance_tracker.record_task_metric(
            _make_task_metric(
                started_at=_NOW - timedelta(hours=2),
                completed_at=_NOW - timedelta(hours=1),
            ),
        )
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        # Both task_completed and task_started from same record
        types = {d["event_type"] for d in body["data"]}
        assert types == {"task_completed", "task_started"}

    async def test_filter_by_type(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        performance_tracker: PerformanceTracker,
    ) -> None:
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                timestamp=_NOW - timedelta(hours=1),
                event_type=LifecycleEventType.HIRED,
            ),
        )
        await performance_tracker.record_task_metric(
            _make_task_metric(completed_at=_NOW - timedelta(hours=2)),
        )
        resp = test_client.get(
            "/api/v1/activities",
            params={"type": "task_completed"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["event_type"] == "task_completed"

    async def test_filter_by_agent_id(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        other_id = "00000000-0000-0000-0000-000000000bbb"
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                agent_id=_AGENT_ID,
                timestamp=_NOW - timedelta(hours=1),
            ),
        )
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                agent_id=other_id,
                agent_name="bob",
                timestamp=_NOW - timedelta(hours=2),
            ),
        )
        resp = test_client.get(
            "/api/v1/activities",
            params={"agent_id": _AGENT_ID},
        )
        assert resp.status_code == 200

    def test_last_n_hours_default(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Default last_n_hours is 24."""
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200

    @pytest.mark.parametrize("hours", [24, 48, 168])
    def test_last_n_hours_valid_values(
        self,
        test_client: TestClient[Any],
        hours: int,
    ) -> None:
        """24, 48, and 168 are valid values."""
        resp = test_client.get(
            "/api/v1/activities",
            params={"last_n_hours": hours},
        )
        assert resp.status_code == 200

    def test_last_n_hours_invalid(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Invalid last_n_hours values should return 400."""
        resp = test_client.get(
            "/api/v1/activities",
            params={"last_n_hours": 12},
        )
        assert resp.status_code == 400

    def test_pagination(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/activities",
            params={"limit": 10},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "pagination" in body
        assert body["pagination"]["limit"] == 10
        assert body["pagination"]["has_more"] is False

    async def test_graceful_degradation_no_performance_tracker(
        self,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Endpoint still returns lifecycle events when perf tracker fails."""
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )
        app = await _make_app_with_broken_tracker(fake_persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/api/v1/activities",
                headers=make_auth_headers("ceo"),
            )
            assert resp.status_code == 200

    async def test_feed_with_cost_records(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
    ) -> None:
        record = CostRecord(
            agent_id=_AGENT_ID,
            task_id="task-001",
            provider="test-provider",
            model="test-medium-001",
            input_tokens=500,
            output_tokens=100,
            cost=0.005,
            currency="EUR",
            timestamp=_NOW - timedelta(hours=1),
        )
        await cost_tracker.record(record)
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["event_type"] == "cost_incurred"

    async def test_filter_by_type_cost_incurred(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        cost_tracker: CostTracker,
    ) -> None:
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )
        record = CostRecord(
            agent_id=_AGENT_ID,
            task_id="task-001",
            provider="test-provider",
            model="test-medium-001",
            input_tokens=500,
            output_tokens=100,
            cost=0.005,
            currency="EUR",
            timestamp=_NOW - timedelta(hours=2),
        )
        await cost_tracker.record(record)
        resp = test_client.get(
            "/api/v1/activities",
            params={"type": "cost_incurred"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["event_type"] == "cost_incurred"

    async def test_feed_with_tool_invocations(
        self,
        test_client: TestClient[Any],
        tool_invocation_tracker: ToolInvocationTracker,
    ) -> None:
        record = ToolInvocationRecord(
            agent_id=_AGENT_ID,
            tool_name="read_file",
            is_success=True,
            timestamp=_NOW - timedelta(hours=1),
        )
        await tool_invocation_tracker.record(record)
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["event_type"] == "tool_used"

    async def test_feed_with_delegation_events(
        self,
        test_client: TestClient[Any],
        delegation_record_store: DelegationRecordStore,
    ) -> None:
        record = DelegationRecord(
            delegation_id="del-001",
            delegator_id="agent-manager",
            delegatee_id="agent-worker",
            original_task_id="task-parent",
            delegated_task_id="del-abc123",
            timestamp=_NOW - timedelta(hours=1),
        )
        delegation_record_store.record_sync(record)
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        # Org-wide: both sent and received
        types = {d["event_type"] for d in body["data"]}
        assert types == {"delegation_sent", "delegation_received"}

    async def test_filter_delegation_by_agent(
        self,
        test_client: TestClient[Any],
        delegation_record_store: DelegationRecordStore,
    ) -> None:
        record = DelegationRecord(
            delegation_id="del-001",
            delegator_id=_AGENT_ID,
            delegatee_id="agent-worker",
            original_task_id="task-parent",
            delegated_task_id="del-abc123",
            timestamp=_NOW - timedelta(hours=1),
        )
        delegation_record_store.record_sync(record)
        # Filter by delegator agent -- only sees delegation_sent
        resp = test_client.get(
            "/api/v1/activities",
            params={"agent_id": _AGENT_ID},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Only lifecycle events for this agent + delegation_sent
        delegation_events = [
            d for d in body["data"] if d["event_type"].startswith("delegation")
        ]
        assert len(delegation_events) == 1
        assert delegation_events[0]["event_type"] == "delegation_sent"

    async def test_graceful_degradation_broken_tool_tracker(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        tool_invocation_tracker: ToolInvocationTracker,
    ) -> None:
        """Endpoint returns 200 when tool tracker raises."""
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )

        # Monkey-patch get_records to raise
        async def _raise(**_kw: object) -> None:
            msg = "simulated failure"
            raise RuntimeError(msg)

        tool_invocation_tracker.get_records = _raise  # type: ignore[assignment]

        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200

    async def test_graceful_degradation_broken_delegation_store(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        delegation_record_store: DelegationRecordStore,
    ) -> None:
        """Endpoint returns 200 when delegation store raises."""
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )

        # Monkey-patch methods to raise
        async def _raise(*_a: object, **_kw: object) -> None:
            msg = "simulated failure"
            raise RuntimeError(msg)

        delegation_record_store.get_all_records = _raise  # type: ignore[assignment]
        delegation_record_store.get_records_as_delegator = _raise  # type: ignore[assignment]
        delegation_record_store.get_records_as_delegatee = _raise  # type: ignore[assignment]

        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200

    def test_filter_by_invalid_type_returns_400(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Invalid event_type values are rejected with 400."""
        resp = test_client.get(
            "/api/v1/activities",
            params={"type": "bogus_event"},
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize("event_type", list(ActivityEventType))
    def test_filter_by_valid_enum_types(
        self,
        test_client: TestClient[Any],
        event_type: ActivityEventType,
    ) -> None:
        """All 13 known event type values are accepted."""
        resp = test_client.get(
            "/api/v1/activities",
            params={"type": event_type.value},
        )
        assert resp.status_code == 200

    async def test_service_unavailable_error_propagates(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        performance_tracker: PerformanceTracker,
    ) -> None:
        """ServiceUnavailableError from a fetcher results in 503."""
        from synthorg.core.domain_errors import ServiceUnavailableError

        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )

        def _raise_svc(**_kw: object) -> None:
            msg = "service down"
            raise ServiceUnavailableError(msg)

        performance_tracker.get_task_metrics = _raise_svc  # type: ignore[assignment]

        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 503


def _make_cost_record(
    *,
    agent_id: str = _AGENT_ID,
    timestamp: datetime | None = None,
) -> CostRecord:
    return CostRecord(
        agent_id=agent_id,
        task_id="task-001",
        provider="test-provider",
        model="test-medium-001",
        input_tokens=500,
        output_tokens=100,
        cost=0.005,
        currency="EUR",
        timestamp=timestamp or _NOW - timedelta(hours=1),
    )


class TestCostEventRedaction:
    """Cost event descriptions are redacted for read-only roles."""

    @pytest.mark.parametrize("role", ["ceo", "manager", "pair_programmer"])
    async def test_write_role_sees_full_cost_description(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
        role: str,
    ) -> None:
        """Write roles see the unredacted cost description."""
        await cost_tracker.record(_make_cost_record())
        resp = test_client.get(
            "/api/v1/activities",
            headers=make_auth_headers(role),
        )
        assert resp.status_code == 200
        desc = resp.json()["data"][0]["description"]
        assert "test-medium-001" in desc
        assert "tokens" in desc

    @pytest.mark.parametrize("role", ["observer", "board_member"])
    async def test_read_role_sees_redacted_cost_description(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
        role: str,
    ) -> None:
        """Read-only roles see redacted cost descriptions."""
        await cost_tracker.record(_make_cost_record())
        resp = test_client.get(
            "/api/v1/activities",
            headers=make_auth_headers(role),
        )
        assert resp.status_code == 200
        desc = resp.json()["data"][0]["description"]
        assert "test-medium-001" not in desc
        assert "500+100 tokens" in desc
        assert desc == "API call (500+100 tokens)"

    async def test_missing_auth_user_sees_redacted(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
    ) -> None:
        """Read-only role triggers redaction (fail-closed)."""
        await cost_tracker.record(_make_cost_record())
        # Use a read-only role to verify fail-closed redaction behavior.
        # The default test_client uses CEO (write role); observer proves
        # that non-write users get redacted descriptions.
        resp = test_client.get(
            "/api/v1/activities",
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 200
        body = resp.json()
        cost_events = [e for e in body["data"] if e["event_type"] == "cost_incurred"]
        for event in cost_events:
            assert "test-medium-001" not in event["description"]


class TestDegradedSources:
    """Degraded data sources are reported in the response."""

    def test_no_degradation_returns_empty_list(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        assert resp.json()["degraded_sources"] == []

    async def test_degraded_performance_tracker(
        self,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Performance tracker failure is reported in degraded_sources."""
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )
        app = await _make_app_with_broken_tracker(fake_persistence)
        with TestClient(app) as client:
            resp = client.get(
                "/api/v1/activities",
                headers=make_auth_headers("ceo"),
            )
            assert resp.status_code == 200
            assert "performance_tracker" in resp.json()["degraded_sources"]

    async def test_degraded_tool_tracker(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        tool_invocation_tracker: ToolInvocationTracker,
    ) -> None:
        """Tool tracker failure is reported in degraded_sources."""
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )

        async def _raise(**_kw: object) -> None:
            msg = "simulated failure"
            raise RuntimeError(msg)

        tool_invocation_tracker.get_records = _raise  # type: ignore[assignment]

        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        assert "tool_invocation_tracker" in body["degraded_sources"]

    async def test_degraded_delegation_store(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        delegation_record_store: DelegationRecordStore,
    ) -> None:
        """Delegation store failure is reported in degraded_sources."""
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )

        async def _raise(*_a: object, **_kw: object) -> None:
            msg = "simulated failure"
            raise RuntimeError(msg)

        delegation_record_store.get_all_records = _raise  # type: ignore[assignment]
        delegation_record_store.get_records_as_delegator = _raise  # type: ignore[assignment]
        delegation_record_store.get_records_as_delegatee = _raise  # type: ignore[assignment]

        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        body = resp.json()
        assert "delegation_record_store" in body["degraded_sources"]

    async def test_degraded_cost_tracker(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        cost_tracker: CostTracker,
    ) -> None:
        """Cost tracker failure is reported in degraded_sources."""
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )

        async def _raise(**_kw: object) -> None:
            msg = "simulated failure"
            raise RuntimeError(msg)

        cost_tracker.get_records = _raise  # type: ignore[assignment]

        resp = test_client.get("/api/v1/activities")
        assert resp.status_code == 200
        assert "cost_tracker" in resp.json()["degraded_sources"]

    async def test_degraded_budget_config(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Budget config failure is reported in degraded_sources."""
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(timestamp=_NOW - timedelta(hours=1)),
        )
        # Break the config resolver
        app_state = test_client.app.state.app_state
        original = app_state.config_resolver.get_budget_config
        app_state.config_resolver.get_budget_config = AsyncMock(
            side_effect=RuntimeError("simulated failure"),
        )
        try:
            resp = test_client.get("/api/v1/activities")
            assert resp.status_code == 200
            assert "budget_config" in resp.json()["degraded_sources"]
        finally:
            app_state.config_resolver.get_budget_config = original


class TestActivityFeedLifecycleCap:
    """Lifecycle cap is sourced from ``app_state.api_bridge_config``.

    The controller no longer carries a hardcoded fallback constant;
    the cap flows through the bridge-config snapshot that
    ``_apply_bridge_config`` populates at startup and the
    :class:`ApiBridgeSettingsSubscriber` hot-swaps on operator change.
    """

    async def test_default_cap_passed_to_list_events(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}
        original = fake_persistence.lifecycle_events.list_events

        async def _spy(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return await original(**kwargs)

        monkeypatch.setattr(fake_persistence.lifecycle_events, "list_events", _spy)
        resp = test_client.get("/api/v1/activities")

        assert resp.status_code == 200
        app_state = test_client.app.state.app_state
        assert (
            captured["limit"]
            == app_state.api_bridge_config.max_lifecycle_events_per_query
        )

    async def test_swapped_cap_takes_effect_immediately(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.settings.bridge_configs import ApiBridgeConfig

        app_state = test_client.app.state.app_state
        previous = app_state.api_bridge_config
        app_state.swap_api_bridge_config(
            previous.model_copy(update={"max_lifecycle_events_per_query": 137}),
        )
        captured: dict[str, Any] = {}
        original = fake_persistence.lifecycle_events.list_events

        async def _spy(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return await original(**kwargs)

        monkeypatch.setattr(fake_persistence.lifecycle_events, "list_events", _spy)
        try:
            resp = test_client.get("/api/v1/activities")
        finally:
            app_state.swap_api_bridge_config(previous)

        assert resp.status_code == 200
        assert captured["limit"] == 137
        # Confirm the snapshot still holds an ``ApiBridgeConfig`` after
        # restoration -- the swap path is reversible.
        assert isinstance(app_state.api_bridge_config, ApiBridgeConfig)


class TestActivitiesControllerSurface:
    """The legacy fallback symbols are gone after the refactor."""

    def test_max_lifecycle_events_constant_removed(self) -> None:
        from synthorg.api.controllers import activities as mod

        assert not hasattr(mod, "_MAX_LIFECYCLE_EVENTS")

    def test_resolve_lifecycle_cap_helper_removed(self) -> None:
        from synthorg.api.controllers import activities as mod

        assert not hasattr(mod, "_resolve_lifecycle_cap")
        assert not hasattr(mod, "_lifecycle_cap_fallback_logged")
