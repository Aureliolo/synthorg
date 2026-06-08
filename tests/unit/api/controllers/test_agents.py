"""Tests for agent controller."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from synthorg.config.schema import AgentConfig, RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import Complexity, TaskType, ToolAccessLevel
from synthorg.core.types import stable_agent_id
from synthorg.hr.enums import LifecycleEventType, TrendDirection
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)


@pytest.mark.unit
class TestAgentController:
    async def test_list_agents_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get("/api/v1/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_list_agents_with_data(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        from synthorg.api.auth.service import AuthService
        from synthorg.budget.tracker import CostTracker
        from tests._shared import build_test_app as create_app
        from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

        config = RootConfig(
            company_name="test",
            agents=(
                AgentConfig(
                    name="alice",
                    role="developer",
                    department="eng",
                ),
            ),
        )
        auth_service: AuthService = _make_test_auth_service()
        _seed_test_users(fake_persistence, auth_service)
        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )
        app = create_app(
            config=config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=CostTracker(),
            auth_service=auth_service,
            settings_service=settings_service,
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = await client.get("/api/v1/agents")
            body = resp.json()
            assert body["data"][0]["name"] == "alice"

    async def test_get_agent_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/agents/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_oversized_agent_name_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_name = "x" * 129
        resp = await async_test_client.get(f"/api/v1/agents/{long_name}")
        assert resp.status_code == 400


@pytest.mark.unit
class TestAgentControllerDbOverride:
    """Test that DB-stored settings override YAML agents."""

    async def test_db_agents_override_config(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        from synthorg.api.auth.service import AuthService
        from synthorg.budget.tracker import CostTracker
        from tests._shared import build_test_app as create_app
        from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

        config = RootConfig(
            company_name="test",
            agents=(AgentConfig(name="yaml-agent", role="dev", department="eng"),),
        )
        auth_service: AuthService = _make_test_auth_service()
        _seed_test_users(fake_persistence, auth_service)
        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )

        db_agents = [
            {"name": "db-agent-1", "role": "qa", "department": "eng"},
            {"name": "db-agent-2", "role": "pm", "department": "ops"},
        ]
        await settings_service.set("company", "agents", json.dumps(db_agents))

        app = create_app(
            config=config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=CostTracker(),
            auth_service=auth_service,
            settings_service=settings_service,
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = await client.get("/api/v1/agents")
            assert resp.status_code == 200
            body = resp.json()
            names = {a["name"] for a in body["data"]}
            assert names == {"db-agent-1", "db-agent-2"}

            detail_resp = await client.get(
                f"/api/v1/agents/{stable_agent_id('db-agent-1')}"
            )
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["data"]["name"] == "db-agent-1"


# ── Helpers for performance/activity/history tests ────────────
# Note: Similar factories exist in tests/unit/hr/test_activity.py with
# different defaults (simple string IDs vs UUID format) because controller
# tests need UUID-valid agent_id for AgentIdentity construction.


_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)
_AGENT_NAME = "test-agent"
# Derive the id the same way production does, so the sub-resource routes
# (addressed by id) exercise the real name->id derivation rather than an
# arbitrary literal that happens to match the registered identity.
_AGENT_ID = str(stable_agent_id(_AGENT_NAME))


def _make_identity(
    *,
    agent_id: str = _AGENT_ID,
    name: str = _AGENT_NAME,
) -> AgentIdentity:
    return AgentIdentity(
        id=UUID(agent_id),
        name=name,
        role="developer",
        department="eng",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=_NOW.date(),
    )


def _make_task_metric(  # noqa: PLR0913
    *,
    completed_at: datetime = _NOW,
    agent_id: str = _AGENT_ID,
    task_id: str = "task-001",
    is_success: bool = True,
    duration_seconds: float = 60.0,
    cost: float = 0.05,
) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=agent_id,
        task_id=task_id,
        task_type=TaskType.DEVELOPMENT,
        completed_at=completed_at,
        is_success=is_success,
        duration_seconds=duration_seconds,
        cost=cost,
        currency="EUR",
        turns_used=5,
        tokens_used=1000,
        complexity=Complexity.MEDIUM,
    )


def _make_lifecycle_event(  # noqa: PLR0913
    *,
    event_type: LifecycleEventType = LifecycleEventType.HIRED,
    timestamp: datetime = _NOW,
    agent_id: str = _AGENT_ID,
    agent_name: str = _AGENT_NAME,
    details: str = "",
    initiated_by: str = "system",
) -> AgentLifecycleEvent:
    return AgentLifecycleEvent(
        agent_id=agent_id,
        agent_name=agent_name,
        event_type=event_type,
        timestamp=timestamp,
        initiated_by=initiated_by,
        details=details,
    )


# ── Performance endpoint tests ────────────────────────────────


@pytest.mark.unit
class TestAgentPerformance:
    async def test_performance_returns_summary(
        self,
        async_test_client: LoopAsyncClient,
        performance_tracker: PerformanceTracker,
        agent_registry: AgentRegistryService,
    ) -> None:
        identity = _make_identity()
        await agent_registry.register(identity)
        for i in range(3):
            await performance_tracker.record_task_metric(
                _make_task_metric(
                    completed_at=_NOW - timedelta(days=i),
                    task_id=f"task-{i}",
                ),
            )

        resp = await async_test_client.get(f"/api/v1/agents/{_AGENT_ID}/performance")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["agent_name"] == _AGENT_NAME
        assert data["trend_direction"] in {d.value for d in TrendDirection}
        assert isinstance(data["windows"], list)
        assert len(data["windows"]) >= 1
        assert isinstance(data["trends"], list)

    async def test_performance_empty_metrics(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        await agent_registry.register(_make_identity())

        resp = await async_test_client.get(f"/api/v1/agents/{_AGENT_ID}/performance")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["tasks_completed_total"] == 0
        assert data["tasks_completed_7d"] == 0
        assert data["tasks_completed_30d"] == 0
        assert data["success_rate_percent"] is None
        assert data["quality_score"] is None
        assert data["collaboration_score"] is None

    async def test_performance_agent_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/agents/nonexistent/performance")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    # The controller's "no agent registry -> 503" branch is defensive code
    # for callers that build ``AppState`` directly without a registry. It is
    # covered at the property level in ``tests/unit/api/test_state.py``, which
    # asserts ``AppState.agent_registry`` raises ``ServiceUnavailableError``
    # when the registry is ``None``; Litestar maps that to HTTP 503. An
    # HTTP-level test would need a second full app with ``agent_registry=None``
    # just to exercise one branch, so the property-level coverage suffices.


# ── Activity endpoint tests ───────────────────────────────────


@pytest.mark.unit
class TestAgentActivity:
    async def test_activity_returns_merged_timeline(
        self,
        async_test_client: LoopAsyncClient,
        performance_tracker: PerformanceTracker,
        agent_registry: AgentRegistryService,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        identity = _make_identity()
        await agent_registry.register(identity)

        # Seed lifecycle event
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                event_type=LifecycleEventType.HIRED,
                timestamp=_NOW - timedelta(days=10),
                details="Hired as developer",
            ),
        )

        # Seed task metric
        await performance_tracker.record_task_metric(
            _make_task_metric(
                completed_at=_NOW - timedelta(days=5),
                task_id="task-100",
            ),
        )

        resp = await async_test_client.get(f"/api/v1/agents/{_AGENT_ID}/activity")

        assert resp.status_code == 200
        body = resp.json()
        events = body["data"]
        # Most recent first
        assert events[0]["event_type"] == "task_completed"
        assert "task-100" in events[0]["related_ids"]["task_id"]
        assert "succeeded" in events[0]["description"]
        assert events[1]["event_type"] == "hired"
        assert events[1]["description"] == "Hired as developer"

    async def test_activity_pagination(
        self,
        async_test_client: LoopAsyncClient,
        performance_tracker: PerformanceTracker,
        agent_registry: AgentRegistryService,
    ) -> None:
        await agent_registry.register(_make_identity())
        for i in range(5):
            await performance_tracker.record_task_metric(
                _make_task_metric(
                    completed_at=_NOW - timedelta(hours=i),
                    task_id=f"task-{i}",
                ),
            )

        # Walk the first page (no cursor) -> collect next_cursor -> walk page 2.
        resp1 = await async_test_client.get(
            f"/api/v1/agents/{_AGENT_ID}/activity",
            params={"limit": 2},
        )
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["pagination"]["limit"] == 2
        assert body1["pagination"]["has_more"] is True
        assert body1["pagination"]["next_cursor"] is not None
        assert len(body1["data"]) == 2

        resp2 = await async_test_client.get(
            f"/api/v1/agents/{_AGENT_ID}/activity",
            params={"limit": 2, "cursor": body1["pagination"]["next_cursor"]},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["pagination"]["limit"] == 2
        assert len(body2["data"]) == 2
        # Verify the cursor actually advanced: page 2's items must be
        # distinct from page 1's, otherwise the controller could pass
        # metadata checks while serving the same slice every call.
        page1_task_ids = [
            item.get("task_id") for item in body1["data"] if "task_id" in item
        ]
        page2_task_ids = [
            item.get("task_id") for item in body2["data"] if "task_id" in item
        ]
        assert set(page1_task_ids).isdisjoint(set(page2_task_ids))

        # Walk to the terminal page (limit=2 across 5 items -> page 3
        # has 1 item and clears has_more + next_cursor per the
        # PaginationMeta consistency validator).
        resp3 = await async_test_client.get(
            f"/api/v1/agents/{_AGENT_ID}/activity",
            params={"limit": 2, "cursor": body2["pagination"]["next_cursor"]},
        )
        assert resp3.status_code == 200
        body3 = resp3.json()
        assert body3["pagination"]["has_more"] is False
        assert body3["pagination"]["next_cursor"] is None
        assert len(body3["data"]) == 1

    async def test_activity_empty(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        await agent_registry.register(_make_identity())

        resp = await async_test_client.get(f"/api/v1/agents/{_AGENT_ID}/activity")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_activity_agent_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/agents/nonexistent/activity")
        assert resp.status_code == 404


# ── History endpoint tests ────────────────────────────────────


@pytest.mark.unit
class TestAgentHistory:
    async def test_history_returns_career_events(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await agent_registry.register(_make_identity())

        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                event_type=LifecycleEventType.HIRED,
                timestamp=_NOW - timedelta(days=30),
                details="Hired as developer",
            ),
        )
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                event_type=LifecycleEventType.STATUS_CHANGED,
                timestamp=_NOW - timedelta(days=20),
                details="Status changed to idle",
            ),
        )
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                event_type=LifecycleEventType.PROMOTED,
                timestamp=_NOW - timedelta(days=10),
                details="Promoted to senior",
            ),
        )

        resp = await async_test_client.get(f"/api/v1/agents/{_AGENT_ID}/history")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # STATUS_CHANGED is filtered out; only HIRED and PROMOTED remain
        assert len(data) == 2
        assert data[0]["event_type"] == "hired"
        assert data[1]["event_type"] == "promoted"

    async def test_history_chronological_order(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await agent_registry.register(_make_identity())

        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                event_type=LifecycleEventType.PROMOTED,
                timestamp=_NOW - timedelta(days=1),
            ),
        )
        await fake_persistence.lifecycle_events.save(
            _make_lifecycle_event(
                event_type=LifecycleEventType.HIRED,
                timestamp=_NOW - timedelta(days=30),
            ),
        )

        resp = await async_test_client.get(f"/api/v1/agents/{_AGENT_ID}/history")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Ascending order (hired first, then promoted)
        assert data[0]["event_type"] == "hired"
        assert data[1]["event_type"] == "promoted"

    async def test_history_empty(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        await agent_registry.register(_make_identity())

        resp = await async_test_client.get(f"/api/v1/agents/{_AGENT_ID}/history")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data == []

    async def test_history_agent_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/agents/nonexistent/history")
        assert resp.status_code == 404


# -- Health endpoint tests ------------------------------------------------


@pytest.mark.unit
class TestAgentHealth:
    async def test_health_returns_composite_data(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        await agent_registry.register(_make_identity())
        resp = await async_test_client.get(
            f"/api/v1/agents/{_AGENT_ID}/health",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["agent_id"] == _AGENT_ID
        assert data["agent_name"] == _AGENT_NAME
        assert data["lifecycle_status"] == "active"
        assert data["performance"] is not None

    async def test_health_trust_none_when_not_tracked(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        """Trust is None when TrustService has no state for agent."""
        await agent_registry.register(_make_identity())
        resp = await async_test_client.get(
            f"/api/v1/agents/{_AGENT_ID}/health",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["trust"] is None

    async def test_health_agent_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/agents/nonexistent/health")
        assert resp.status_code == 404

    async def test_health_performance_fields(
        self,
        async_test_client: LoopAsyncClient,
        agent_registry: AgentRegistryService,
    ) -> None:
        """Verify performance sub-object has correct field types."""
        await agent_registry.register(_make_identity())
        resp = await async_test_client.get(
            f"/api/v1/agents/{_AGENT_ID}/health",
        )
        assert resp.status_code == 200
        perf = resp.json()["data"]["performance"]
        assert perf["quality_score"] is None or isinstance(
            perf["quality_score"],
            (int, float),
        )
        assert perf["collaboration_score"] is None or isinstance(
            perf["collaboration_score"],
            (int, float),
        )
        # trend is a TrendDirection string or None
        valid_trends = {t.value for t in TrendDirection}
        assert perf["trend"] is None or perf["trend"] in valid_trends


# -- _extract_quality_trend unit tests ------------------------------------


@pytest.mark.unit
class TestExtractQualityTrend:
    def test_returns_direction_when_quality_present(self) -> None:
        from synthorg.api.controllers.agents import _extract_quality_trend

        class _Trend:
            def __init__(self, name: str, direction: TrendDirection) -> None:
                self.metric_name = name
                self.direction = direction

        class _Snap:
            trends: list[_Trend] = [  # noqa: RUF012
                _Trend("latency", TrendDirection.STABLE),
                _Trend("quality", TrendDirection.IMPROVING),
            ]

        result = _extract_quality_trend(_Snap())
        assert result is TrendDirection.IMPROVING

    def test_returns_none_when_no_quality_trend(self) -> None:
        from synthorg.api.controllers.agents import _extract_quality_trend

        class _Snap:
            trends: list[object] = []  # noqa: RUF012

        assert _extract_quality_trend(_Snap()) is None

    def test_returns_none_when_only_non_quality_trends(self) -> None:
        from synthorg.api.controllers.agents import _extract_quality_trend

        class _Trend:
            def __init__(self, name: str) -> None:
                self.metric_name = name
                self.direction = TrendDirection.STABLE

        class _Snap:
            trends = [_Trend("latency"), _Trend("collaboration")]  # noqa: RUF012

        assert _extract_quality_trend(_Snap()) is None


# -- Model validation tests -----------------------------------------------


@pytest.mark.unit
class TestHealthModels:
    def test_trust_summary_score_without_evaluated_at_rejected(
        self,
    ) -> None:
        from synthorg.api.controllers.agents import TrustSummary

        with pytest.raises(ValidationError, match="score requires"):
            TrustSummary(
                level=ToolAccessLevel.STANDARD,
                score=0.8,
                last_evaluated_at=None,
            )

    def test_trust_summary_score_with_evaluated_at_accepted(self) -> None:
        from synthorg.api.controllers.agents import TrustSummary

        ts = TrustSummary(
            level=ToolAccessLevel.STANDARD,
            score=0.8,
            last_evaluated_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        assert ts.score == 0.8

    def test_trust_summary_no_score_no_evaluated_at_accepted(self) -> None:
        from synthorg.api.controllers.agents import TrustSummary

        ts = TrustSummary(level=ToolAccessLevel.STANDARD)
        assert ts.score is None
        assert ts.last_evaluated_at is None

    def test_performance_summary_rejects_nan(self) -> None:
        from synthorg.api.controllers.agents import PerformanceSummary

        with pytest.raises(ValidationError):
            PerformanceSummary(quality_score=float("nan"))

    def test_performance_summary_rejects_out_of_range(self) -> None:
        from synthorg.api.controllers.agents import PerformanceSummary

        with pytest.raises(ValidationError):
            PerformanceSummary(quality_score=11.0)

    def test_performance_summary_accepts_valid(self) -> None:
        from synthorg.api.controllers.agents import PerformanceSummary

        ps = PerformanceSummary(
            quality_score=5.0,
            collaboration_score=8.0,
            trend=TrendDirection.IMPROVING,
        )
        assert ps.quality_score == 5.0
        assert ps.trend == TrendDirection.IMPROVING
