"""Tests for department health endpoint."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from litestar.testing import TestClient

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import AgentConfig, RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import AgentStatus, Complexity, TaskType
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests.unit.api.conftest import (
    FakeMessageBus,
    make_auth_headers,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend

_NOW = datetime.now(UTC)
_AGENT_ID_A = "00000000-0000-0000-0000-000000000aaa"
_AGENT_ID_B = "00000000-0000-0000-0000-000000000bbb"
_HEADERS = make_auth_headers("ceo")


def _make_identity(
    *,
    agent_id: str,
    name: str,
    department: str = "eng",
    status: AgentStatus = AgentStatus.ACTIVE,
) -> AgentIdentity:
    return AgentIdentity(
        id=UUID(agent_id),
        name=name,
        role="developer",
        department=department,
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=_NOW.date(),
        status=status,
    )


def _make_cost_record(
    *,
    agent_id: str,
    timestamp: datetime,
    cost: float = 0.01,
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
    agent_id: str,
    completed_at: datetime,
    is_success: bool = True,
    cost: float = 0.01,
) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=agent_id,
        task_id="task-001",
        task_type=TaskType.DEVELOPMENT,
        completed_at=completed_at,
        is_success=is_success,
        duration_seconds=10.0,
        cost=cost,
        currency="EUR",
        turns_used=2,
        tokens_used=150,
        complexity=Complexity.SIMPLE,
    )


def _build_dept_client(
    *,
    fake_message_bus: FakeMessageBus,
    config: RootConfig,
    cost_tracker: CostTracker | None = None,
    performance_tracker: PerformanceTracker | None = None,
    agent_registry: AgentRegistryService | None = None,
) -> TestClient[Any]:
    """Build a TestClient with the given config for department tests.

    Constructs a fresh :class:`FakePersistenceBackend` per call so the
    settings repository the controller reads through is empty at test
    start. Sharing the session-scoped ``fake_persistence`` fixture
    leaks settings written by other tests (via ``_shared_app``
    consumers) into the config-resolver lookup, which surfaces as
    spurious 404s on departments the test config explicitly declares.
    """
    from synthorg.api.app import create_app
    from synthorg.api.auth.service import AuthService
    from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

    fake_persistence = FakePersistenceBackend()
    fake_persistence.mark_connected()
    auth_service: AuthService = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    settings_service = SettingsService(
        repository=fake_persistence.settings,  # type: ignore[arg-type]
        registry=get_registry(),
    )
    app = create_app(
        config=config,
        persistence=fake_persistence,  # type: ignore[arg-type]
        message_bus=fake_message_bus,
        cost_tracker=cost_tracker or CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
        performance_tracker=performance_tracker or PerformanceTracker(),
        agent_registry=agent_registry or AgentRegistryService(),
    )
    return TestClient(app)


# ── Tests ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestDepartmentHealth:
    def test_department_not_found(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/departments/nonexistent/health")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_auth_required(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            "/api/v1/departments/eng/health",
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status_code == 401

    def test_empty_department(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Department exists but has no agents."""
        from synthorg.core.company import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=50.0),),
        )
        with _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
        ) as client:
            resp = client.get(
                "/api/v1/departments/eng/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["department_name"] == "eng"
            assert data["agent_count"] == 0
            assert data["active_agent_count"] == 0
            assert data["utilization_percent"] == 0.0
            assert data["avg_performance_score"] is None
            assert data["department_cost_7d"] == 0.0
            assert data["collaboration_score"] is None

    async def test_with_agents_and_data(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Full scenario with agents, costs, and performance data."""
        from synthorg.core.company import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=50.0),),
            agents=(
                AgentConfig(name="alice", role="dev", department="eng"),
                AgentConfig(name="bob", role="dev", department="eng"),
            ),
        )

        # Set up agent registry with 1 active, 1 inactive
        registry = AgentRegistryService()
        identity_a = _make_identity(
            agent_id=_AGENT_ID_A,
            name="alice",
            department="eng",
            status=AgentStatus.ACTIVE,
        )
        identity_b = _make_identity(
            agent_id=_AGENT_ID_B,
            name="bob",
            department="eng",
            status=AgentStatus.ON_LEAVE,
        )
        await registry.register(identity_a)
        await registry.register(identity_b)

        # Set up cost tracker with records in last 7 days
        cost_tracker = CostTracker()
        await cost_tracker.record(
            _make_cost_record(
                agent_id=_AGENT_ID_A,
                timestamp=_NOW - timedelta(days=1),
                cost=0.50,
            ),
        )
        await cost_tracker.record(
            _make_cost_record(
                agent_id=_AGENT_ID_B,
                timestamp=_NOW - timedelta(days=2),
                cost=0.30,
            ),
        )

        # Set up performance tracker
        perf = PerformanceTracker()
        await perf.record_task_metric(
            _make_task_metric(
                agent_id=_AGENT_ID_A,
                completed_at=_NOW - timedelta(days=1),
            ),
        )

        with _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
            cost_tracker=cost_tracker,
            performance_tracker=perf,
            agent_registry=registry,
        ) as client:
            resp = client.get(
                "/api/v1/departments/eng/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["department_name"] == "eng"
            assert data["agent_count"] == 2
            assert data["active_agent_count"] == 1
            assert data["utilization_percent"] == 50.0
            assert data["department_cost_7d"] == 0.80
            assert isinstance(data["cost_trend"], list)
            # Performance scores may be None if snapshot
            # resolution failed, but they should be present
            assert "avg_performance_score" in data
            assert "collaboration_score" in data

    def test_other_department_agents_excluded(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Agents from other departments are excluded."""
        from synthorg.core.company import Department

        config = RootConfig(
            company_name="test",
            departments=(
                Department(name="eng", budget_percent=50.0),
                Department(name="sales", budget_percent=50.0),
            ),
            agents=(
                AgentConfig(name="alice", role="dev", department="eng"),
                AgentConfig(name="bob", role="rep", department="sales"),
            ),
        )
        with _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
        ) as client:
            resp = client.get(
                "/api/v1/departments/eng/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["agent_count"] == 1

    def test_cost_trend_is_daily_sparkline(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """cost_trend should contain daily-bucketed data points."""
        from synthorg.core.company import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=100.0),),
            agents=(AgentConfig(name="alice", role="dev", department="eng"),),
        )
        with _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
        ) as client:
            resp = client.get(
                "/api/v1/departments/eng/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            trend = data["cost_trend"]
            assert isinstance(trend, list)
            # Should have 7 daily buckets
            assert len(trend) == 7
            for pt in trend:
                assert "timestamp" in pt
                assert "value" in pt


# ── _mean_optional unit tests ─────────────────────────────────


@pytest.mark.unit
class TestMeanOptional:
    def test_empty_list(self) -> None:
        from synthorg.api.controllers._department_health import _mean_optional

        assert _mean_optional([]) is None

    def test_all_none(self) -> None:
        from synthorg.api.controllers._department_health import _mean_optional

        assert _mean_optional([None, None]) is None

    def test_mixed_values(self) -> None:
        from synthorg.api.controllers._department_health import _mean_optional

        assert _mean_optional([5.0, None, 10.0]) == 7.5

    def test_all_present(self) -> None:
        from synthorg.api.controllers._department_health import _mean_optional

        assert _mean_optional([3.0, 6.0, 9.0]) == 6.0


# ── DepartmentHealth model validation tests ───────────────────


@pytest.mark.unit
class TestDepartmentHealthModel:
    def test_active_exceeds_total_rejected(self) -> None:
        from synthorg.api.controllers._department_health import DepartmentHealth

        with pytest.raises(ValueError, match="exceeds agent_count"):
            DepartmentHealth(
                department_name="eng",
                agent_count=2,
                active_agent_count=5,
                department_cost_7d=0.0,
                cost_trend=(),
            )

    def test_utilization_percent_computed(self) -> None:
        from synthorg.api.controllers._department_health import DepartmentHealth

        health = DepartmentHealth(
            department_name="eng",
            agent_count=4,
            active_agent_count=2,
            department_cost_7d=0.0,
            cost_trend=(),
        )
        assert health.utilization_percent == 50.0

    def test_utilization_percent_zero_agents(self) -> None:
        from synthorg.api.controllers._department_health import DepartmentHealth

        health = DepartmentHealth(
            department_name="eng",
            agent_count=0,
            active_agent_count=0,
            department_cost_7d=0.0,
            cost_trend=(),
        )
        assert health.utilization_percent == 0.0


# ── Currency aggregation invariant ────────────────────────────


@pytest.mark.unit
class TestAggregateDeptCost:
    """Same-currency invariant in ``_aggregate_dept_cost``.

    Per the budget design (``docs/design/budget.md``) and audit
    finding 126, cost aggregation across distinct currencies is
    rejected at the aggregator boundary -- the helper raises
    ``MixedCurrencyAggregationError`` rather than silently summing
    a meaningless number.
    """

    def _make_record(
        self,
        agent: str,
        *,
        currency: str = "USD",
        cost: float = 0.10,
    ) -> CostRecord:
        return CostRecord(
            agent_id=agent,
            task_id="task-001",
            provider="test-provider",
            model="test-small-001",
            input_tokens=100,
            output_tokens=50,
            cost=cost,
            currency=currency,
            timestamp=_NOW,
        )

    @pytest.mark.parametrize(
        ("records_spec", "agent_ids", "expected"),
        [
            # Single-currency happy path: totals sum across same-currency records.
            pytest.param(
                (("a", "USD", 0.10), ("b", "USD", 0.20)),
                frozenset({"a", "b"}),
                {"total": 0.30, "currency": "USD"},
                id="single-currency-aggregates",
            ),
            # No matching records: total=0, currency=None.
            pytest.param(
                (),
                frozenset({"a"}),
                {"total": 0.0, "currency": None},
                id="no-records-returns-none-currency",
            ),
            # Mixed-currency: must raise; reuse the same harness.
            pytest.param(
                (("a", "USD", 0.10), ("b", "EUR", 0.10)),
                frozenset({"a", "b"}),
                {"raises": frozenset({"USD", "EUR"})},
                id="mixed-currency-raises",
            ),
        ],
    )
    def test_aggregate_dept_cost(
        self,
        records_spec: tuple[tuple[str, str, float], ...],
        agent_ids: frozenset[str],
        expected: dict[str, object],
    ) -> None:
        from synthorg.api.controllers._department_health import _aggregate_dept_cost
        from synthorg.budget.errors import MixedCurrencyAggregationError

        records = tuple(
            self._make_record(agent_id, currency=cur, cost=cost)
            for agent_id, cur, cost in records_spec
        )
        if "raises" in expected:
            with pytest.raises(MixedCurrencyAggregationError) as exc_info:
                _aggregate_dept_cost(records, agent_ids, _NOW)
            assert exc_info.value.currencies == expected["raises"]
            return
        # Use the named ``DepartmentCostAggregate`` fields rather than
        # tuple positions so a future field reorder doesn't silently
        # break this assertion.
        aggregate = _aggregate_dept_cost(records, agent_ids, _NOW)
        assert aggregate.total_cost == pytest.approx(expected["total"])
        assert aggregate.currency == expected["currency"]

    def test_aggregate_dept_cost_propagates_dept_name_on_mixed_currency(
        self,
    ) -> None:
        """``dept_name`` flows through to the raised exception's project_id.

        Lets operators identify which department triggered the
        mixed-currency rejection without correlating timestamps against
        the calling endpoint.
        """
        from synthorg.api.controllers._department_health import _aggregate_dept_cost
        from synthorg.budget.errors import MixedCurrencyAggregationError
        from synthorg.core.types import NotBlankStr

        records = (
            self._make_record("a", currency="USD"),
            self._make_record("b", currency="EUR"),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc_info:
            _aggregate_dept_cost(
                records,
                frozenset({"a", "b"}),
                _NOW,
                dept_name=NotBlankStr("Engineering"),
            )
        assert exc_info.value.project_id == "Engineering"


# ── ExceptionGroup fallback test ──────────────────────────────


@pytest.mark.unit
class TestDepartmentHealthDegradation:
    async def test_degraded_when_cost_tracker_fails(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Endpoint returns degraded health when first-stage queries fail."""
        from unittest.mock import AsyncMock

        from synthorg.core.company import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=100.0),),
            agents=(AgentConfig(name="alice", role="dev", department="eng"),),
        )
        cost_tracker = CostTracker()
        cost_tracker.get_records = AsyncMock(  # type: ignore[method-assign]
            spec=CostTracker.get_records,
            side_effect=RuntimeError("simulated cost failure"),
        )
        with _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
            cost_tracker=cost_tracker,
        ) as client:
            resp = client.get(
                "/api/v1/departments/eng/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            # Degraded: zeroed metrics
            assert data["active_agent_count"] == 0
            assert data["utilization_percent"] == 0.0
            assert data["department_cost_7d"] == 0.0
            assert data["avg_performance_score"] is None
