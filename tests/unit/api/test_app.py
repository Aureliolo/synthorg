"""Tests for application factory."""

from typing import Any

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from synthorg.api.app import create_app


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
        assert data["info"]["title"] == "SynthOrg API"

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
            ("Permissions-Policy", "geolocation=(), camera=(), microphone=()"),
            (
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            ),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("Cache-Control", "no-store"),
        ],
    )
    def test_security_response_headers(
        self,
        test_client: TestClient[Any],
        header: str,
        expected: str,
    ) -> None:
        response = test_client.get("/docs/openapi.json")
        assert response.headers.get(header) == expected


@pytest.mark.unit
class TestAppLifecycle:
    async def test_startup_partial_failure_cleanup(
        self,
        root_config: Any,
    ) -> None:
        """Persistence ok, bus fails → persistence cleaned up."""
        from synthorg.api.app import _safe_startup
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.state import AppState
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
            await _safe_startup(persistence, bus, None, None, None, app_state)
        # Persistence should have been disconnected during cleanup
        assert not persistence.is_connected

    async def test_shutdown_error_handling(self) -> None:
        """Shutdown errors are logged but don't propagate."""
        from synthorg.api.app import _safe_shutdown
        from tests.unit.api.conftest import FakePersistenceBackend

        persistence = FakePersistenceBackend()

        async def failing_disconnect() -> None:
            msg = "disconnect boom"
            raise RuntimeError(msg)

        persistence.disconnect = failing_disconnect  # type: ignore[method-assign]

        # Should not raise even when disconnect fails
        await _safe_shutdown(None, None, None, None, persistence)

    async def test_task_engine_failure_cleans_up(
        self,
        root_config: Any,
    ) -> None:
        """Task engine start fails → persistence + bus cleaned up."""
        from unittest.mock import MagicMock

        from synthorg.api.app import _safe_startup
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.state import AppState
        from tests.unit.api.conftest import (
            FakeMessageBus,
            FakePersistenceBackend,
        )

        persistence = FakePersistenceBackend()
        bus = FakeMessageBus()
        mock_te = MagicMock()
        mock_te.start = MagicMock(side_effect=RuntimeError("engine boom"))
        mock_te.stop = MagicMock()

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=persistence,
        )

        with pytest.raises(RuntimeError, match="engine boom"):
            await _safe_startup(persistence, bus, None, mock_te, None, app_state)

        # Persistence and bus should be cleaned up
        assert not persistence.is_connected
        assert not bus.is_running

    async def test_shutdown_task_engine_failure_does_not_propagate(self) -> None:
        """Task engine stop failure during shutdown is logged, not raised."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.api.app import _safe_shutdown

        mock_te = MagicMock()
        mock_te.stop = AsyncMock(side_effect=RuntimeError("stop boom"))

        # Should not raise even when task engine stop fails
        await _safe_shutdown(mock_te, None, None, None, None)

    async def test_meeting_scheduler_lifecycle(
        self,
        root_config: Any,
    ) -> None:
        """Meeting scheduler start/stop are called during lifecycle."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.api.app import _safe_shutdown, _safe_startup
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.state import AppState
        from tests.unit.api.conftest import (
            FakeMessageBus,
            FakePersistenceBackend,
        )

        persistence = FakePersistenceBackend()
        bus = FakeMessageBus()
        mock_sched = MagicMock()
        mock_sched.start = AsyncMock()
        mock_sched.stop = AsyncMock()

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=persistence,
        )

        await _safe_startup(
            persistence,
            bus,
            None,
            None,
            mock_sched,
            app_state,
        )
        mock_sched.start.assert_awaited_once()

        await _safe_shutdown(None, mock_sched, None, None, None)
        mock_sched.stop.assert_awaited_once()


@pytest.mark.unit
class TestTryStop:
    """Tests for the _try_stop helper."""

    async def test_try_stop_success(self) -> None:
        """Successful coroutine runs without error."""
        from synthorg.api.app import _try_stop

        called = False

        async def noop() -> None:
            nonlocal called
            called = True

        await _try_stop(noop(), "event", "error msg")
        assert called is True

    async def test_try_stop_exception_swallowed(self) -> None:
        """Non-fatal exceptions are swallowed (logged)."""
        from synthorg.api.app import _try_stop

        async def fail() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        # Should not raise
        await _try_stop(fail(), "event", "error msg")

    async def test_try_stop_memory_error_reraises(self) -> None:
        """MemoryError is re-raised immediately."""
        from synthorg.api.app import _try_stop

        async def oom() -> None:
            raise MemoryError

        with pytest.raises(MemoryError):
            await _try_stop(oom(), "event", "error msg")

    async def test_try_stop_recursion_error_reraises(self) -> None:
        """RecursionError is re-raised immediately."""
        from synthorg.api.app import _try_stop

        async def recurse() -> None:
            raise RecursionError

        with pytest.raises(RecursionError):
            await _try_stop(recurse(), "event", "error msg")
