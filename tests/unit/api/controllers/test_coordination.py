"""Tests for coordination controller."""

from collections.abc import AsyncGenerator
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.api.auth.service import AuthService
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import AgentStatus, CoordinationTopology
from synthorg.engine.coordination.attribution import (
    CoordinationResultWithAttribution,
)
from synthorg.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationResult,
)
from synthorg.engine.errors import CoordinationPhaseError
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from tests._shared import (
    LoopAsyncClient,
    make_app_state,
)
from tests._shared import (
    build_test_app as create_app,
)
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

_TEST_TASK_ID = "test-coord-task"


def _insert_task(task_engine: TaskEngine, *, task_id: str = _TEST_TASK_ID) -> str:
    """Insert a Task directly into the fake persistence layer.

    ``POST /tasks`` returns 202 + a submission envelope (no task id);
    task materialisation happens asynchronously inside the
    (mocked-here) pipeline spine. Calling ``task_engine.create_task``
    from the test body would cross event loops (the engine starts
    inside the TestClient's ASGI loop and ``_running`` /
    ``_admission_lock`` are bound to it), so we insert the task
    directly into the in-memory repo instead. ``task_engine.get_task``
    reads straight from persistence and therefore picks it up.
    """
    from tests.unit.api.conftest import make_task as _make_task

    task = _make_task(task_id=task_id, project="proj-1", created_by="api")
    persistence = task_engine._persistence
    # ``_persistence.tasks`` returns the FakeTaskRepository; its
    # internal ``_tasks`` dict is the in-memory store coordinator's
    # ``get_task`` reads through.
    repo = persistence.tasks
    repo._tasks[task.id] = task  # type: ignore[attr-defined]  # fake repo
    return task.id


def _make_agent(name: str = "test-agent") -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


def _make_coordination_result(
    task_id: str = "task-001",
    *,
    is_success: bool = True,
) -> CoordinationResultWithAttribution:
    """Build a minimal CoordinationResultWithAttribution for tests."""
    phase = CoordinationPhaseResult(
        phase="decompose",
        success=is_success,
        duration_seconds=0.1,
        error=None if is_success else "test error",
    )
    result = CoordinationResult(
        parent_task_id=task_id,
        topology=CoordinationTopology.SAS,
        phases=(phase,),
        total_duration_seconds=0.5,
        total_cost=0.01,
    )
    return CoordinationResultWithAttribution(result=result)


@pytest.fixture
def mock_coordinator() -> AsyncMock:
    """Mock MultiAgentCoordinator."""
    coordinator = AsyncMock()
    coordinator.coordinate.return_value = _make_coordination_result()
    return coordinator


@pytest.fixture
def local_agent_registry() -> AgentRegistryService:
    return AgentRegistryService()


@pytest.fixture
async def coordination_ctx(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    auth_service: AuthService,
    mock_coordinator: AsyncMock,
    local_agent_registry: AgentRegistryService,
) -> AsyncGenerator[SimpleNamespace]:
    """Test client + the bound task engine for coordinator tests.

    Returns a ``SimpleNamespace(client, task_engine)``: the client is
    used for HTTP, the task engine for pre-creating coordinatable
    tasks. The public ``POST /tasks`` is a 202 board handoff that
    creates the task via the work pipeline spine, not the engine
    directly, so coordinator tests bypass it via ``_insert_task``.
    """
    from tests.unit.api.conftest import _seed_test_users

    _seed_test_users(fake_persistence, auth_service)

    from synthorg.engine.task_engine_config import (
        TaskEngineConfig,
    )
    from tests.unit.engine.task_engine_helpers import (
        FakeMessageBus as EngineMessageBus,
    )
    from tests.unit.engine.task_engine_helpers import (
        FakePersistence,
    )

    task_engine = TaskEngine(
        config=TaskEngineConfig(),
        persistence=FakePersistence(),  # type: ignore[arg-type]
        message_bus=EngineMessageBus(),  # type: ignore[arg-type]
    )

    import synthorg.settings.definitions  # noqa: F401 -- trigger registration
    from synthorg.settings.registry import get_registry
    from synthorg.settings.service import SettingsService

    root_config = RootConfig(company_name="test")
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )

    # A scripted provider keeps ``has_active_provider`` true so runtime
    # checks remain satisfied; these tests seed tasks via
    # ``_insert_task`` rather than the public ``POST /tasks`` board
    # handoff.
    from synthorg.config.provider_schema import ProviderConfig
    from synthorg.providers.registry import ProviderRegistry

    app = create_app(
        config=root_config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        task_engine=task_engine,
        coordinator=mock_coordinator,
        agent_registry=local_agent_registry,
        settings_service=settings_service,
        provider_registry=ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")},
        ),
    )
    async with LoopAsyncClient(app) as client:
        client.headers.update(make_auth_headers("ceo"))
        yield SimpleNamespace(client=client, task_engine=task_engine)


@pytest.mark.unit
class TestCoordinationControllerHappyPath:
    async def test_coordinate_task_success(
        self,
        coordination_ctx: SimpleNamespace,
        mock_coordinator: AsyncMock,
        local_agent_registry: AgentRegistryService,
    ) -> None:
        agent = _make_agent()
        await local_agent_registry.register(agent)

        task_id = _insert_task(coordination_ctx.task_engine)
        mock_coordinator.coordinate.return_value = _make_coordination_result(task_id)

        resp = await coordination_ctx.client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["parent_task_id"] == task_id
        assert body["data"]["topology"] == "sas"
        assert body["data"]["is_success"] is True
        assert body["data"]["wave_count"] == 0
        assert len(body["data"]["phases"]) == 1

    async def test_coordinate_with_specific_agents(
        self,
        coordination_ctx: SimpleNamespace,
        mock_coordinator: AsyncMock,
        local_agent_registry: AgentRegistryService,
    ) -> None:
        agent = _make_agent("alice")
        await local_agent_registry.register(agent)

        task_id = _insert_task(coordination_ctx.task_engine)
        mock_coordinator.coordinate.return_value = _make_coordination_result(task_id)

        resp = await coordination_ctx.client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={"agent_names": ["alice"]},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify the coordinator received the resolved agent
        mock_coordinator.coordinate.assert_awaited_once()
        call_context = mock_coordinator.coordinate.call_args[0][0]
        resolved_names = [a.name for a in call_context.available_agents]
        assert resolved_names == ["alice"]

    async def test_coordinate_with_failed_phases(
        self,
        coordination_ctx: SimpleNamespace,
        mock_coordinator: AsyncMock,
        local_agent_registry: AgentRegistryService,
    ) -> None:
        """Coordination returns is_success=False for failed phases."""
        agent = _make_agent()
        await local_agent_registry.register(agent)

        task_id = _insert_task(coordination_ctx.task_engine)
        mock_coordinator.coordinate.return_value = _make_coordination_result(
            task_id, is_success=False
        )

        resp = await coordination_ctx.client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["is_success"] is False


@pytest.mark.unit
class TestCoordinationControllerErrors:
    async def test_task_not_found(
        self,
        coordination_ctx: SimpleNamespace,
    ) -> None:
        resp = await coordination_ctx.client.post(
            "/api/v1/tasks/nonexistent/coordinate",
            json={},
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_unknown_agent_name(
        self,
        coordination_ctx: SimpleNamespace,
    ) -> None:
        task_id = _insert_task(coordination_ctx.task_engine)
        resp = await coordination_ctx.client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={"agent_names": ["nonexistent-agent"]},
        )
        assert resp.status_code == 422
        assert "not found" in resp.json()["error"].lower()

    async def test_no_active_agents(
        self,
        coordination_ctx: SimpleNamespace,
    ) -> None:
        task_id = _insert_task(coordination_ctx.task_engine)
        resp = await coordination_ctx.client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={},
        )
        assert resp.status_code == 422
        assert "no active agents" in resp.json()["error"].lower()

    async def test_coordination_phase_error(
        self,
        coordination_ctx: SimpleNamespace,
        mock_coordinator: AsyncMock,
        local_agent_registry: AgentRegistryService,
    ) -> None:
        agent = _make_agent()
        await local_agent_registry.register(agent)

        task_id = _insert_task(coordination_ctx.task_engine)
        mock_coordinator.coordinate.side_effect = CoordinationPhaseError(
            "Decomposition failed: test error",
            phase="decompose",
        )

        resp = await coordination_ctx.client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={},
        )
        assert resp.status_code == 422
        assert "coordination failed at phase" in resp.json()["error"].lower()


@pytest.mark.unit
class TestCoordinationPathParamValidation:
    async def test_oversized_task_id_rejected(
        self,
        coordination_ctx: SimpleNamespace,
    ) -> None:
        long_id = "x" * 129
        resp = await coordination_ctx.client.post(
            f"/api/v1/tasks/{long_id}/coordinate",
            json={},
        )
        assert resp.status_code == 400


@pytest.mark.unit
class TestCoordinationControllerNoCoordinator:
    async def test_503_when_coordinator_not_configured(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """503 when coordinator is not configured (uses shared client)."""
        from tests.unit.api.conftest import make_task

        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task

        resp = await async_test_client.post(
            f"/api/v1/tasks/{task.id}/coordinate",
            json={},
        )
        assert resp.status_code == 503


@pytest.mark.unit
class TestResolveAgentsBatchLookup:
    """_resolve_agents must batch agent lookups via registry.get_by_names."""

    async def test_resolves_via_batch_call(self) -> None:
        """Hits get_by_names exactly once; never falls back to get_by_name."""
        from synthorg.api.controllers.coordination import CoordinationController
        from synthorg.api.dto import CoordinateTaskRequest

        agents = [_make_agent(name) for name in ("alice", "bob", "carol")]
        mock_registry = AsyncMock(spec=AgentRegistryService)
        mock_registry.get_by_names.return_value = tuple(agents)

        app_state = make_app_state(agent_registry=mock_registry)
        controller = CoordinationController(owner=None)  # type: ignore[arg-type]
        data = CoordinateTaskRequest(agent_names=("alice", "bob", "carol"))

        result = await controller._resolve_agents(
            app_state,
            data,
            "task-batch-001",
        )

        assert mock_registry.get_by_names.await_count == 1
        assert mock_registry.get_by_name.await_count == 0
        assert [a.name for a in result] == ["alice", "bob", "carol"]

    async def test_raises_validation_error_on_missing_agent(self) -> None:
        """A None entry in the batch result surfaces as ValidationError."""
        from synthorg.api.controllers.coordination import CoordinationController
        from synthorg.api.dto import CoordinateTaskRequest
        from synthorg.core.domain_errors import ValidationError

        alice = _make_agent("alice")
        mock_registry = AsyncMock(spec=AgentRegistryService)
        mock_registry.get_by_names.return_value = (alice, None)

        app_state = make_app_state(agent_registry=mock_registry)
        controller = CoordinationController(owner=None)  # type: ignore[arg-type]
        data = CoordinateTaskRequest(agent_names=("alice", "missing"))

        with pytest.raises(ValidationError, match="missing"):
            await controller._resolve_agents(
                app_state,
                data,
                "task-batch-002",
            )
