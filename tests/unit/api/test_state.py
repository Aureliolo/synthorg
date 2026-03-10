"""Tests for application state accessors."""

import pytest

from ai_company.api.approval_store import ApprovalStore
from ai_company.api.errors import ServiceUnavailableError
from ai_company.api.state import AppState
from ai_company.config.schema import RootConfig
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
        from ai_company.budget.tracker import CostTracker

        tracker = CostTracker()
        state = _make_state(cost_tracker=tracker)
        assert state.cost_tracker is tracker

    def test_auth_service_raises_when_none(self) -> None:
        state = _make_state(auth_service=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.auth_service

    def test_auth_service_returns_when_set(self) -> None:
        from ai_company.api.auth.config import AuthConfig
        from ai_company.api.auth.service import AuthService

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state(auth_service=svc)
        assert state.auth_service is svc

    def test_set_auth_service_succeeds_once(self) -> None:
        from ai_company.api.auth.config import AuthConfig
        from ai_company.api.auth.service import AuthService

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state()
        state.set_auth_service(svc)
        assert state.auth_service is svc

    def test_set_auth_service_twice_raises(self) -> None:
        from ai_company.api.auth.config import AuthConfig
        from ai_company.api.auth.service import AuthService

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state(auth_service=svc)
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_auth_service(svc)
