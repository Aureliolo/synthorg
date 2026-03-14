"""Tests for application state accessors."""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.errors import ServiceUnavailableError
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
)


def _make_state(**overrides: object) -> AppState:
    defaults: dict[str, object] = {
        "config": RootConfig(company_name="test"),
        "approval_store": ApprovalStore(),
    }
    defaults.update(overrides)
    return AppState(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestAppStateAccessors:
    def test_persistence_raises_when_none(self) -> None:
        state = _make_state(persistence=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.persistence

    def test_message_bus_raises_when_none(self) -> None:
        state = _make_state(message_bus=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.message_bus

    def test_cost_tracker_raises_when_none(self) -> None:
        state = _make_state(cost_tracker=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.cost_tracker

    async def test_persistence_returns_when_set(self) -> None:
        backend = FakePersistenceBackend()
        await backend.connect()
        state = _make_state(persistence=backend)
        assert state.persistence is backend

    async def test_message_bus_returns_when_set(self) -> None:
        bus = FakeMessageBus()
        await bus.start()
        state = _make_state(message_bus=bus)
        assert state.message_bus is bus

    def test_cost_tracker_returns_when_set(self) -> None:
        from synthorg.budget.tracker import CostTracker

        tracker = CostTracker()
        state = _make_state(cost_tracker=tracker)
        assert state.cost_tracker is tracker

    def test_auth_service_raises_when_none(self) -> None:
        state = _make_state(auth_service=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.auth_service

    def test_auth_service_returns_when_set(self) -> None:
        from synthorg.api.auth.config import AuthConfig
        from synthorg.api.auth.service import AuthService

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state(auth_service=svc)
        assert state.auth_service is svc

    def test_set_auth_service_succeeds_once(self) -> None:
        from synthorg.api.auth.config import AuthConfig
        from synthorg.api.auth.service import AuthService

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state()
        state.set_auth_service(svc)
        assert state.auth_service is svc

    def test_set_auth_service_twice_raises(self) -> None:
        from synthorg.api.auth.config import AuthConfig
        from synthorg.api.auth.service import AuthService

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state(auth_service=svc)
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_auth_service(svc)


@pytest.mark.unit
class TestAppStateTaskEngine:
    """Tests for task_engine property, has_task_engine, set_task_engine."""

    def test_task_engine_raises_when_none(self) -> None:
        state = _make_state(task_engine=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.task_engine

    def test_task_engine_returns_when_set(self) -> None:
        from unittest.mock import MagicMock

        engine = MagicMock()
        state = _make_state(task_engine=engine)
        assert state.task_engine is engine

    def test_has_task_engine_false_when_none(self) -> None:
        state = _make_state(task_engine=None)
        assert state.has_task_engine is False

    def test_has_task_engine_true_when_set(self) -> None:
        from unittest.mock import MagicMock

        engine = MagicMock()
        state = _make_state(task_engine=engine)
        assert state.has_task_engine is True

    def test_set_task_engine_succeeds_once(self) -> None:
        from unittest.mock import MagicMock

        engine = MagicMock()
        state = _make_state()
        state.set_task_engine(engine)
        assert state.task_engine is engine

    def test_set_task_engine_twice_raises(self) -> None:
        from unittest.mock import MagicMock

        engine = MagicMock()
        state = _make_state(task_engine=engine)
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_task_engine(engine)


@pytest.mark.unit
class TestAppStateCoordinator:
    """Tests for coordinator property and has_coordinator."""

    def test_coordinator_raises_when_none(self) -> None:
        state = _make_state(coordinator=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.coordinator

    def test_coordinator_returns_when_set(self) -> None:
        from unittest.mock import MagicMock

        coordinator = MagicMock()
        state = _make_state(coordinator=coordinator)
        assert state.coordinator is coordinator

    def test_has_coordinator_false_when_none(self) -> None:
        state = _make_state(coordinator=None)
        assert state.has_coordinator is False

    def test_has_coordinator_true_when_set(self) -> None:
        from unittest.mock import MagicMock

        coordinator = MagicMock()
        state = _make_state(coordinator=coordinator)
        assert state.has_coordinator is True


@pytest.mark.unit
class TestAppStateAgentRegistry:
    """Tests for agent_registry property."""

    def test_agent_registry_raises_when_none(self) -> None:
        state = _make_state(agent_registry=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.agent_registry

    def test_agent_registry_returns_when_set(self) -> None:
        from synthorg.hr.registry import AgentRegistryService

        registry = AgentRegistryService()
        state = _make_state(agent_registry=registry)
        assert state.agent_registry is registry

    def test_has_agent_registry_false_when_none(self) -> None:
        state = _make_state(agent_registry=None)
        assert state.has_agent_registry is False

    def test_has_agent_registry_true_when_set(self) -> None:
        from synthorg.hr.registry import AgentRegistryService

        registry = AgentRegistryService()
        state = _make_state(agent_registry=registry)
        assert state.has_agent_registry is True
