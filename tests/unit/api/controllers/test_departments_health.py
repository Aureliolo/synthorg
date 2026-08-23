"""Tests for department health endpoint."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.config.agent_schema import AgentConfig
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.hr.enums import AgentStatus
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient, as_uuid
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
            model_id="test-basic-001",
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
        model="test-basic-001",
        input_tokens=100,
        output_tokens=50,
        cost=cost,
        currency="EUR",
        timestamp=timestamp,
    )


def _make_inprogress_task(
    *,
    label: str,
    assigned_to: str,
    status: TaskStatus = TaskStatus.IN_PROGRESS,
) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description="Department utilisation fixture.",
        type=TaskType.DEVELOPMENT,
        project="project-1",
        priority=Priority.MEDIUM,
        status=status,
        assigned_to=assigned_to,
        created_by="engine",
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


async def _build_dept_client(
    *,
    fake_message_bus: FakeMessageBus,
    config: RootConfig,
    cost_tracker: CostTracker | None = None,
    performance_tracker: PerformanceTracker | None = None,
    agent_registry: AgentRegistryService | None = None,
    tasks: tuple[Task, ...] = (),
) -> LoopAsyncClient:
    """Build a LoopAsyncClient with the given config for department tests.

    Constructs a fresh :class:`FakePersistenceBackend` per call so the
    settings repository the controller reads through is empty at test
    start. Sharing the session-scoped ``fake_persistence`` fixture
    leaks settings written by other tests (via ``_shared_app``
    consumers) into the config-resolver lookup, which surfaces as
    spurious 404s on departments the test config explicitly declares.

    ``tasks`` seeds the task repository so utilisation (busy assignees of
    ``IN_PROGRESS`` tasks) can be exercised end-to-end.
    """
    from synthorg.api.auth.service import AuthService
    from tests._shared import build_test_app as create_app
    from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

    fake_persistence = FakePersistenceBackend()
    fake_persistence.mark_connected()
    for task in tasks:
        await fake_persistence.tasks.save(task)
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
        cost_tracker=cost_tracker or CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
        performance_tracker=performance_tracker or PerformanceTracker(),
        agent_registry=agent_registry or AgentRegistryService(),
    )
    return LoopAsyncClient(app)


# ── Tests ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestDepartmentHealth:
    async def test_department_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/departments/nonexistent/health")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_auth_required(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/departments/eng/health",
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status_code == 401

    async def test_empty_department(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Department exists but has no agents."""
        from synthorg.core.company_departments import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=50.0),),
        )
        async with await _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
        ) as client:
            resp = await client.get(
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

    async def test_the_whole_org_reads_in_one_request(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """The org-health panel wants all of them, and paid a request each.

        Six departments against a per-operation budget of thirty a minute
        meant five dashboard views exhausted it, and the refusals rendered as
        "no departments configured".
        """
        from synthorg.core.company_departments import Department

        config = RootConfig(
            company_name="test",
            departments=(
                Department(name="eng", budget_percent=50.0),
                Department(name="design", budget_percent=50.0),
            ),
        )
        async with await _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
        ) as client:
            resp = await client.get("/api/v1/departments/health", headers=_HEADERS)

            assert resp.status_code == 200
            data = resp.json()["data"]
            assert [entry["department_name"] for entry in data] == ["eng", "design"]

    async def test_the_collection_route_is_not_read_as_a_department_name(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """``/departments/health`` is the collection, not a department named
        "health": the per-department route would 404 on it."""
        from synthorg.core.company_departments import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=50.0),),
        )
        async with await _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
        ) as client:
            resp = await client.get("/api/v1/departments/health", headers=_HEADERS)

            assert resp.status_code == 200

    async def test_with_agents_and_data(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Full scenario with agents, costs, and performance data."""
        from synthorg.core.company_departments import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=50.0),),
            agents=(
                AgentConfig(name="alice", role="dev", department="eng"),
                AgentConfig(name="bob", role="dev", department="eng"),
            ),
        )

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

        # Utilisation is runtime, not lifecycle: alice is executing a task and
        # bob is not, so 1 of 2 agents is busy regardless of employment status.
        tasks = (_make_inprogress_task(label="t-alice", assigned_to=_AGENT_ID_A),)

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

        async with await _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
            cost_tracker=cost_tracker,
            performance_tracker=perf,
            agent_registry=registry,
            tasks=tasks,
        ) as client:
            resp = await client.get(
                "/api/v1/departments/eng/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["department_name"] == "eng"
            assert data["agent_count"] == 2
            assert data["active_agent_count"] == 1
            assert data["utilization_percent"] == 50.0
            assert data["department_cost_7d"] == pytest.approx(0.80)
            assert isinstance(data["cost_trend"], list)
            # A single recorded metric is below the snapshot min-data-points
            # gate, so the derived score is a deterministic no-data ``None``
            # (not merely "present").
            assert data["avg_performance_score"] is None

    async def test_other_department_agents_excluded(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Agents from other departments are excluded."""
        from synthorg.core.company_departments import Department

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
        async with await _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
        ) as client:
            resp = await client.get(
                "/api/v1/departments/eng/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["agent_count"] == 1

    async def test_cost_trend_is_daily_sparkline(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """cost_trend should contain daily-bucketed data points."""
        from synthorg.core.company_departments import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=100.0),),
            agents=(AgentConfig(name="alice", role="dev", department="eng"),),
        )
        async with await _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
        ) as client:
            resp = await client.get(
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


@pytest.mark.unit
class TestDepartmentHealthScore:
    """Honest health derivation: real task-outcome signal or explicit no-data."""

    async def _eng_client(
        self,
        fake_message_bus: FakeMessageBus,
        *,
        perf: PerformanceTracker,
        tasks: tuple[Task, ...] = (),
    ) -> LoopAsyncClient:
        from synthorg.core.company_departments import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=100.0),),
            agents=(AgentConfig(name="alice", role="dev", department="eng"),),
        )
        registry = AgentRegistryService()
        await registry.register(
            _make_identity(
                agent_id=_AGENT_ID_A,
                name="alice",
                department="eng",
                status=AgentStatus.ACTIVE,
            ),
        )
        return await _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
            performance_tracker=perf,
            agent_registry=registry,
            tasks=tasks,
        )

    async def test_idle_roster_is_zero_utilisation_and_no_health(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """An enabled-but-idle roster reads 0% utilised, not 100%.

        Utilisation is a runtime measure: with no ``IN_PROGRESS`` tasks nobody
        is working, so a fully-employed roster is 0% utilised (never the old
        vanity 100% off the ``AgentStatus.ACTIVE`` lifecycle flag), and health
        stays no-data until runs complete.
        """
        perf = PerformanceTracker()  # no metrics recorded, no tasks in flight
        async with await self._eng_client(fake_message_bus, perf=perf) as client:
            resp = await client.get("/api/v1/departments/eng/health", headers=_HEADERS)
            data = resp.json()["data"]
            assert data["active_agent_count"] == 0
            assert data["utilization_percent"] == 0.0
            assert data["total_runs"] == 0
            assert data["health_score"] is None
            assert data["task_success_rate"] is None

    async def test_busy_agent_drives_utilisation(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """A single-agent dept with that agent mid-task reads 100% utilised."""
        perf = PerformanceTracker()
        tasks = (_make_inprogress_task(label="t-alice", assigned_to=_AGENT_ID_A),)
        async with await self._eng_client(
            fake_message_bus, perf=perf, tasks=tasks
        ) as client:
            resp = await client.get("/api/v1/departments/eng/health", headers=_HEADERS)
            data = resp.json()["data"]
            assert data["active_agent_count"] == 1
            assert data["utilization_percent"] == 100.0

    async def test_only_in_progress_tasks_count_as_utilised(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """A non-``IN_PROGRESS`` task (e.g. awaiting review) is not utilisation."""
        perf = PerformanceTracker()
        tasks = (
            _make_inprogress_task(
                label="t-alice",
                assigned_to=_AGENT_ID_A,
                status=TaskStatus.IN_REVIEW,
            ),
        )
        async with await self._eng_client(
            fake_message_bus, perf=perf, tasks=tasks
        ) as client:
            resp = await client.get("/api/v1/departments/eng/health", headers=_HEADERS)
            data = resp.json()["data"]
            assert data["active_agent_count"] == 0
            assert data["utilization_percent"] == 0.0

    async def test_health_score_from_real_success_rate(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        recent = _NOW - timedelta(hours=1)
        perf = PerformanceTracker()
        for _ in range(3):
            await perf.record_task_metric(
                _make_task_metric(
                    agent_id=_AGENT_ID_A, completed_at=recent, is_success=True
                ),
            )
        await perf.record_task_metric(
            _make_task_metric(
                agent_id=_AGENT_ID_A, completed_at=recent, is_success=False
            ),
        )
        async with await self._eng_client(fake_message_bus, perf=perf) as client:
            resp = await client.get("/api/v1/departments/eng/health", headers=_HEADERS)
            data = resp.json()["data"]
            assert data["total_runs"] == 4
            assert data["task_success_rate"] == 0.75
            assert data["health_score"] == 75.0

    async def test_health_score_none_below_min_runs(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Below the min-runs gate the score is no-data, never a guess."""
        perf = PerformanceTracker()
        await perf.record_task_metric(
            _make_task_metric(
                agent_id=_AGENT_ID_A,
                completed_at=_NOW - timedelta(hours=1),
                is_success=True,
            ),
        )
        async with await self._eng_client(fake_message_bus, perf=perf) as client:
            resp = await client.get("/api/v1/departments/eng/health", headers=_HEADERS)
            data = resp.json()["data"]
            assert data["total_runs"] == 1
            assert data["health_score"] is None

    async def test_runs_outside_the_window_are_excluded(
        self,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Only runs within ``department_health_window_days`` (default 7) count."""
        recent = _NOW - timedelta(hours=1)
        stale = _NOW - timedelta(days=30)  # outside the 7-day window
        perf = PerformanceTracker()
        for _ in range(3):
            await perf.record_task_metric(
                _make_task_metric(
                    agent_id=_AGENT_ID_A, completed_at=recent, is_success=True
                ),
            )
        for _ in range(5):
            await perf.record_task_metric(
                _make_task_metric(
                    agent_id=_AGENT_ID_A, completed_at=stale, is_success=False
                ),
            )
        async with await self._eng_client(fake_message_bus, perf=perf) as client:
            resp = await client.get("/api/v1/departments/eng/health", headers=_HEADERS)
            data = resp.json()["data"]
            # The 5 stale failures are excluded; only the 3 recent successes
            # count, so health reads 100% rather than being dragged down.
            assert data["total_runs"] == 3
            assert data["task_success_rate"] == 1.0
            assert data["health_score"] == 100.0


# ── _mean_optional unit tests ─────────────────────────────────


@pytest.mark.unit
class TestMeanOptional:
    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            ([], None),
            ([None, None], None),
            ([5.0, None, 10.0], 7.5),
            ([3.0, 6.0, 9.0], 6.0),
        ],
        ids=["empty", "all-none", "mixed", "all-present"],
    )
    def test_mean_optional(
        self, values: list[float | None], expected: float | None
    ) -> None:
        from synthorg.api.controllers._department_health import _mean_optional

        assert _mean_optional(values) == expected


@pytest.mark.unit
class TestHealthFromOutcomes:
    """The no-data gate + rate math in ``health_from_outcomes``."""

    @pytest.mark.parametrize(
        ("total_runs", "success_count", "min_runs", "expected"),
        [
            (0, 0, 1, None),  # no runs -> no data
            (2, 2, 3, None),  # below the gate -> no data
            (3, 3, 3, 1.0),  # exactly at the gate, all success
            (4, 3, 3, 0.75),  # above the gate, partial success
            (1, 0, 1, 0.0),  # single failed run at min_runs=1
        ],
        ids=["no-runs", "below-gate", "at-gate", "above-gate", "single-fail"],
    )
    def test_gate_and_rate(
        self,
        total_runs: int,
        success_count: int,
        min_runs: int,
        expected: float | None,
    ) -> None:
        from synthorg.api.controllers._department_health_outcomes import (
            health_from_outcomes,
        )

        assert health_from_outcomes(total_runs, success_count, min_runs) == expected


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

    Per the budget design (``docs/design/budget.md``), cost aggregation
    across distinct currencies is rejected at the aggregator boundary: the
    helper raises ``MixedCurrencyAggregationError`` rather than silently
    summing a meaningless number.
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
            model="test-basic-001",
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

        from synthorg.core.company_departments import Department

        config = RootConfig(
            company_name="test",
            departments=(Department(name="eng", budget_percent=100.0),),
            agents=(AgentConfig(name="alice", role="dev", department="eng"),),
        )
        cost_tracker = CostTracker()
        cost_tracker.collect_records = AsyncMock(  # type: ignore[method-assign]
            spec=CostTracker.collect_records,
            side_effect=RuntimeError("simulated cost failure"),
        )
        async with await _build_dept_client(
            fake_message_bus=fake_message_bus,
            config=config,
            cost_tracker=cost_tracker,
        ) as client:
            resp = await client.get(
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
