"""Tests for analytics controller."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.task_enums import Complexity, TaskStatus, TaskType
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

_HEADERS = make_auth_headers("ceo")


# ── Helpers ────────────────────────────────────────────────────


def _make_cost_record(
    *,
    timestamp: datetime,
    cost: float = 0.01,
    agent_id: str = "agent-a",
) -> CostRecord:
    return CostRecord(
        agent_id=agent_id,
        task_id="task-001",
        provider="test-provider",
        model="test-small-001",
        input_tokens=100,
        output_tokens=50,
        cost=cost,
        currency="EUR",
        timestamp=timestamp,
    )


def _make_task_metric(
    *,
    completed_at: datetime,
    is_success: bool = True,
) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id="agent-a",
        task_id="task-001",
        task_type=TaskType.DEVELOPMENT,
        completed_at=completed_at,
        is_success=is_success,
        duration_seconds=10.0,
        cost=0.01,
        currency="EUR",
        turns_used=2,
        tokens_used=150,
        complexity=Complexity.SIMPLE,
    )


# ── Existing overview tests ────────────────────────────────────


@pytest.mark.unit
class TestAnalyticsController:
    async def test_overview_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/overview", headers=_HEADERS
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["total_tasks"] == 0
        expected_statuses = {s.value: 0 for s in TaskStatus}
        assert data["tasks_by_status"] == expected_statuses
        assert data["total_agents"] == 0
        assert data["total_cost"] == 0.0

    async def test_overview_requires_read_access(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/overview",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


# ── Extended overview tests ────────────────────────────────────


@pytest.mark.unit
class TestOverviewExtended:
    """Verify new budget and agent fields in overview."""

    async def test_overview_has_budget_fields(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/overview", headers=_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "budget_remaining" in data
        assert "budget_used_percent" in data
        assert data["budget_remaining"] >= 0.0
        assert data["budget_used_percent"] >= 0.0

    async def test_overview_has_cost_7d_trend(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/overview", headers=_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "cost_7d_trend" in data
        trend = data["cost_7d_trend"]
        assert isinstance(trend, list)
        # 7-day period produces 7-8 daily buckets depending on time of day
        assert len(trend) >= 7
        for point in trend:
            assert "timestamp" in point
            assert "value" in point

    async def test_overview_has_agent_counts(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/overview", headers=_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["active_agents_count"] == 0
        assert data["idle_agents_count"] == 0

    async def test_overview_with_cost_data(
        self,
        cost_tracker: CostTracker,
        async_test_client: LoopAsyncClient,
    ) -> None:
        now = datetime.now(UTC)
        await cost_tracker.record(
            _make_cost_record(timestamp=now - timedelta(hours=1), cost=5.0),
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/overview", headers=_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_cost"] == 5.0
        # Sparkline should have non-zero value for today
        trend = data["cost_7d_trend"]
        values = [p["value"] for p in trend]
        assert sum(values) > 0


# ── Trends endpoint tests ─────────────────────────────────────


@pytest.mark.unit
class TestTrendsEndpoint:
    """GET /analytics/trends."""

    async def test_trends_default_params(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/analytics/trends", headers=_HEADERS)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["period"] == "7d"
        assert data["metric"] == "spend"
        assert data["bucket_size"] == "hour"
        assert isinstance(data["data_points"], list)

    async def test_trends_30d_daily(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/trends",
            params={"period": "30d", "metric": "spend"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["period"] == "30d"
        assert data["bucket_size"] == "day"
        # 30-day period produces 30-31 daily buckets depending on time of day
        assert len(data["data_points"]) >= 30

    async def test_trends_with_cost_data(
        self,
        cost_tracker: CostTracker,
        async_test_client: LoopAsyncClient,
    ) -> None:
        now = datetime.now(UTC)
        await cost_tracker.record(
            _make_cost_record(timestamp=now - timedelta(hours=2), cost=3.0),
        )
        await cost_tracker.record(
            _make_cost_record(timestamp=now - timedelta(hours=1), cost=7.0),
        )
        resp = await async_test_client.get("/api/v1/analytics/trends", headers=_HEADERS)
        assert resp.status_code == 200
        data = resp.json()["data"]
        values = [p["value"] for p in data["data_points"]]
        assert sum(values) == pytest.approx(10.0)

    async def test_trends_tasks_completed(
        self,
        performance_tracker: PerformanceTracker,
        async_test_client: LoopAsyncClient,
    ) -> None:
        now = datetime.now(UTC)
        await performance_tracker.record_task_metric(
            _make_task_metric(completed_at=now - timedelta(hours=1)),
        )
        await performance_tracker.record_task_metric(
            _make_task_metric(completed_at=now - timedelta(hours=2)),
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/trends",
            params={"metric": "tasks_completed"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["metric"] == "tasks_completed"
        values = [p["value"] for p in data["data_points"]]
        assert sum(values) == 2.0

    async def test_trends_success_rate(
        self,
        performance_tracker: PerformanceTracker,
        async_test_client: LoopAsyncClient,
    ) -> None:
        now = datetime.now(UTC)
        await performance_tracker.record_task_metric(
            _make_task_metric(completed_at=now - timedelta(hours=1), is_success=True),
        )
        await performance_tracker.record_task_metric(
            _make_task_metric(completed_at=now - timedelta(hours=1), is_success=False),
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/trends",
            params={"metric": "success_rate"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["metric"] == "success_rate"
        # At least one bucket should have 0.5 rate
        values = [p["value"] for p in data["data_points"] if p["value"] > 0]
        assert len(values) >= 1
        assert values[0] == pytest.approx(0.5)

    async def test_trends_active_agents(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/trends",
            params={"metric": "active_agents"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["metric"] == "active_agents"
        assert isinstance(data["data_points"], list)

    async def test_trends_invalid_period(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/trends",
            params={"period": "99d"},
            headers=_HEADERS,
        )
        assert resp.status_code == 400

    async def test_trends_invalid_metric(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/trends",
            params={"metric": "invalid_metric"},
            headers=_HEADERS,
        )
        assert resp.status_code == 400

    async def test_trends_requires_auth(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/trends",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


# ── Forecast endpoint tests ────────────────────────────────────


@pytest.mark.unit
class TestForecastEndpoint:
    """GET /analytics/forecast."""

    async def test_forecast_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/forecast", headers=_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["horizon_days"] == 14
        assert data["projected_total"] == 0.0
        assert data["avg_daily_spend"] == 0.0
        assert data["confidence"] == 0.0
        assert data["days_until_exhausted"] is None
        assert len(data["daily_projections"]) == 14

    async def test_forecast_with_data(
        self,
        cost_tracker: CostTracker,
        async_test_client: LoopAsyncClient,
    ) -> None:
        now = datetime.now(UTC)
        await cost_tracker.record(
            _make_cost_record(timestamp=now - timedelta(days=2), cost=10.0),
        )
        await cost_tracker.record(
            _make_cost_record(timestamp=now - timedelta(days=1), cost=10.0),
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/forecast",
            params={"horizon_days": 7},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["horizon_days"] == 7
        assert data["avg_daily_spend"] > 0
        assert data["projected_total"] > 0
        assert data["confidence"] > 0
        assert len(data["daily_projections"]) == 7

    async def test_forecast_custom_horizon(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/forecast",
            params={"horizon_days": 30},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["horizon_days"] == 30
        assert len(data["daily_projections"]) == 30

    async def test_forecast_invalid_horizon_too_high(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/forecast",
            params={"horizon_days": 100},
            headers=_HEADERS,
        )
        assert resp.status_code == 400

    async def test_forecast_invalid_horizon_zero(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/forecast",
            params={"horizon_days": 0},
            headers=_HEADERS,
        )
        assert resp.status_code == 400

    async def test_forecast_requires_auth(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/analytics/forecast",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


# ── Graceful degradation tests ─────────────────────────────────


@pytest.mark.unit
class TestAnalyticsGracefulDegradation:
    """Verify fallback behavior when optional services are unavailable."""

    async def test_trends_zero_when_no_data_in_auto_wired_tracker(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Trends returns zero-value buckets when auto-wired tracker has no data."""
        from synthorg.api.auth.service import AuthService
        from tests._shared import build_test_app as create_app
        from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

        config = RootConfig(company_name="test")
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
            resp = await client.get(
                "/api/v1/analytics/trends",
                params={"metric": "tasks_completed"},
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            # Auto-wired tracker has no records, so all buckets are zero.
            assert all(dp["value"] == 0.0 for dp in data["data_points"])


# ── Integration tests ──────────────────────────────────────────


@pytest.mark.integration
class TestAnalyticsControllerDbOverride:
    """Test that DB-stored agents affect analytics agent count."""

    async def test_db_agents_count_in_overview(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        from synthorg.api.auth.service import AuthService
        from tests._shared import build_test_app as create_app
        from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

        config = RootConfig(company_name="test")
        auth_service: AuthService = _make_test_auth_service()
        _seed_test_users(fake_persistence, auth_service)
        settings_service = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
        )

        db_agents = [
            {"name": "a1", "role": "dev", "department": "eng"},
            {"name": "a2", "role": "qa", "department": "eng"},
            {"name": "a3", "role": "pm", "department": "ops"},
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
            client.headers.update(make_auth_headers("ceo"))
            resp = await client.get("/api/v1/analytics/overview")
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["total_agents"] == 3
            assert body["data"]["total_tasks"] == 0
            assert body["data"]["total_cost"] == 0.0
