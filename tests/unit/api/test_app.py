"""Tests for application factory."""

from typing import Any

import pytest
from litestar import Litestar
from litestar.testing import TestClient  # noqa: TC002

from ai_company.api.app import create_app


@pytest.mark.unit
class TestCreateApp:
    def test_returns_litestar_instance(
        self,
        fake_persistence: Any,
        fake_message_bus: Any,
        cost_tracker: Any,
        root_config: Any,
    ) -> None:
        app = create_app(
            config=root_config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=cost_tracker,
        )
        assert isinstance(app, Litestar)

    def test_openapi_schema_accessible(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/docs/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert data["info"]["title"] == "AI Company API"


@pytest.mark.unit
class TestAppLifecycle:
    async def test_startup_partial_failure_cleanup(
        self,
        root_config: Any,
    ) -> None:
        """Persistence ok, bus fails → persistence cleaned up."""
        from ai_company.api.app import _safe_startup
        from ai_company.api.approval_store import ApprovalStore
        from ai_company.api.state import AppState
        from tests.unit.api.conftest import (
            FakeMessageBus,
            FakePersistenceBackend,
        )

        persistence = FakePersistenceBackend()
        bus = FakeMessageBus()
        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=persistence,
        )

        async def failing_start() -> None:
            msg = "bus boom"
            raise RuntimeError(msg)

        bus.start = failing_start  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="bus boom"):
            await _safe_startup(persistence, bus, None, app_state)
        # Persistence should have been disconnected during cleanup
        assert not persistence.is_connected

    async def test_shutdown_error_handling(self) -> None:
        """Shutdown errors are logged but don't propagate."""
        from ai_company.api.app import _safe_shutdown
        from tests.unit.api.conftest import FakePersistenceBackend

        persistence = FakePersistenceBackend()

        async def failing_disconnect() -> None:
            msg = "disconnect boom"
            raise RuntimeError(msg)

        persistence.disconnect = failing_disconnect  # type: ignore[method-assign]

        # Should not raise even when disconnect fails
        await _safe_shutdown(None, None, persistence)
