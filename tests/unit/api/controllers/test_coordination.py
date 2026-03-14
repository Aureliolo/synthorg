"""Tests for coordination controller."""

from collections.abc import Generator
from datetime import date
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.api.auth.service import AuthService
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import (
    AgentStatus,
    CoordinationTopology,
    SeniorityLevel,
)
from synthorg.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationResult,
)
from synthorg.engine.errors import CoordinationPhaseError
from synthorg.hr.registry import AgentRegistryService
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

pytestmark = pytest.mark.timeout(30)


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
) -> CoordinationResult:
    """Build a minimal CoordinationResult for tests."""
    phase = CoordinationPhaseResult(
        phase="decompose",
        success=is_success,
        duration_seconds=0.1,
        error=None if is_success else "test error",
    )
    return CoordinationResult(
        parent_task_id=task_id,
        topology=CoordinationTopology.SAS,
        phases=(phase,),
        total_duration_seconds=0.5,
        total_cost_usd=0.01,
    )


@pytest.fixture
def mock_coordinator() -> AsyncMock:
    """Mock MultiAgentCoordinator."""
    coordinator = AsyncMock()
    coordinator.coordinate.return_value = _make_coordination_result()
    return coordinator


@pytest.fixture
def agent_registry() -> AgentRegistryService:
    return AgentRegistryService()


@pytest.fixture
def coordination_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    auth_service: AuthService,
    mock_coordinator: AsyncMock,
    agent_registry: AgentRegistryService,
) -> Generator[TestClient[Any]]:
    """Test client with coordinator and agent registry configured."""
    from tests.unit.api.conftest import _seed_test_users

    _seed_test_users(fake_persistence, auth_service)

    from synthorg.engine.task_engine import TaskEngine
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

    app = create_app(
        config=RootConfig(company_name="test"),
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        task_engine=task_engine,
        coordinator=mock_coordinator,
        agent_registry=agent_registry,
    )
    with TestClient(app) as client:
        client.headers.update(make_auth_headers("ceo"))
        yield client


@pytest.mark.unit
class TestCoordinationControllerHappyPath:
    async def test_coordinate_task_success(
        self,
        coordination_client: TestClient[Any],
        mock_coordinator: AsyncMock,
        agent_registry: AgentRegistryService,
    ) -> None:
        agent = _make_agent()
        await agent_registry.register(agent)

        resp = coordination_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test task",
                "description": "A test task for coordination",
                "type": "development",
                "project": "proj-1",
                "created_by": "api",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["data"]["id"]

        mock_coordinator.coordinate.return_value = _make_coordination_result(task_id)

        resp = coordination_client.post(
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
        coordination_client: TestClient[Any],
        mock_coordinator: AsyncMock,
        agent_registry: AgentRegistryService,
    ) -> None:
        agent = _make_agent("alice")
        await agent_registry.register(agent)

        resp = coordination_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test task",
                "description": "Coordination test",
                "type": "development",
                "project": "proj-1",
                "created_by": "api",
            },
        )
        task_id = resp.json()["data"]["id"]
        mock_coordinator.coordinate.return_value = _make_coordination_result(task_id)

        resp = coordination_client.post(
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
        coordination_client: TestClient[Any],
        mock_coordinator: AsyncMock,
        agent_registry: AgentRegistryService,
    ) -> None:
        """Coordination returns is_success=False for failed phases."""
        agent = _make_agent()
        await agent_registry.register(agent)

        resp = coordination_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test task",
                "description": "Test",
                "type": "development",
                "project": "proj-1",
                "created_by": "api",
            },
        )
        task_id = resp.json()["data"]["id"]
        mock_coordinator.coordinate.return_value = _make_coordination_result(
            task_id, is_success=False
        )

        resp = coordination_client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["is_success"] is False


@pytest.mark.unit
class TestCoordinationControllerErrors:
    def test_task_not_found(
        self,
        coordination_client: TestClient[Any],
    ) -> None:
        resp = coordination_client.post(
            "/api/v1/tasks/nonexistent/coordinate",
            json={},
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_unknown_agent_name(
        self,
        coordination_client: TestClient[Any],
    ) -> None:
        resp = coordination_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test task",
                "description": "Test",
                "type": "development",
                "project": "proj-1",
                "created_by": "api",
            },
        )
        task_id = resp.json()["data"]["id"]

        resp = coordination_client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={"agent_names": ["nonexistent-agent"]},
        )
        assert resp.status_code == 422
        assert "not found" in resp.json()["error"].lower()

    async def test_no_active_agents(
        self,
        coordination_client: TestClient[Any],
    ) -> None:
        resp = coordination_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test task",
                "description": "Test",
                "type": "development",
                "project": "proj-1",
                "created_by": "api",
            },
        )
        task_id = resp.json()["data"]["id"]

        resp = coordination_client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={},
        )
        assert resp.status_code == 422
        assert "no active agents" in resp.json()["error"].lower()

    async def test_coordination_phase_error(
        self,
        coordination_client: TestClient[Any],
        mock_coordinator: AsyncMock,
        agent_registry: AgentRegistryService,
    ) -> None:
        agent = _make_agent()
        await agent_registry.register(agent)

        resp = coordination_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test task",
                "description": "Test",
                "type": "development",
                "project": "proj-1",
                "created_by": "api",
            },
        )
        task_id = resp.json()["data"]["id"]

        mock_coordinator.coordinate.side_effect = CoordinationPhaseError(
            "Decomposition failed: test error",
            phase="decompose",
        )

        resp = coordination_client.post(
            f"/api/v1/tasks/{task_id}/coordinate",
            json={},
        )
        assert resp.status_code == 422
        assert "coordination failed at phase" in resp.json()["error"].lower()


@pytest.mark.unit
class TestCoordinationControllerNoCoordinator:
    def test_503_when_coordinator_not_configured(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """503 when coordinator is not configured (uses shared client)."""
        from tests.unit.api.conftest import make_task

        task = make_task()
        fake_persistence.tasks._tasks[task.id] = task

        resp = test_client.post(
            f"/api/v1/tasks/{task.id}/coordinate",
            json={},
        )
        assert resp.status_code == 503
