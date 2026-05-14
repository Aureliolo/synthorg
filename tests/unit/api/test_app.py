"""Tests for application factory."""

from pathlib import Path
from typing import Any

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.api.app_builders import _bootstrap_app_logging
from synthorg.api.app_helpers import _resolve_artifact_dir_env
from synthorg.api.middleware import _SECURITY_HEADERS
from synthorg.api.state import AppState
from synthorg.budget.tracker import CostTracker
from synthorg.communication.bus.memory import InMemoryMessageBus
from synthorg.config.schema import RootConfig
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability.config import DEFAULT_SINKS, LogConfig


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

    def test_logging_config_is_disabled(
        self,
        fake_persistence: Any,
        fake_message_bus: Any,
        cost_tracker: Any,
        root_config: Any,
    ) -> None:
        """Litestar must NOT manage logging (structlog owns the pipeline)."""
        app = create_app(
            config=root_config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=cost_tracker,
        )
        assert app.logging_config is None

    def test_openapi_schema_accessible(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/docs/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert data["info"]["title"] == "SynthOrg API"

    @pytest.mark.parametrize(
        ("header", "expected"),
        list(_SECURITY_HEADERS.items()),
    )
    def test_security_response_headers(
        self,
        test_client: TestClient[Any],
        header: str,
        expected: str,
    ) -> None:
        # Use a non-docs endpoint -- /docs paths relax COOP for Scalar UI.
        response = test_client.get("/api/v1/healthz")
        assert response.headers.get(header) == expected

    def test_agent_registry_built_before_auto_wire_meetings(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_persistence: Any,
        fake_message_bus: Any,
        cost_tracker: Any,
        root_config: Any,
    ) -> None:
        """Regression: the registry must exist when auto_wire_meetings runs.

        Moving the registry build after auto_wire_meetings made the meeting
        orchestrator receive `None`, which then 500s on the first request.
        """
        import synthorg.api.app as app_module
        from synthorg.api.auto_wire import auto_wire_meetings as _original_auto_wire

        captured: dict[str, Any] = {}

        def _capturing(**kwargs: Any) -> Any:
            captured["agent_registry"] = kwargs.get("agent_registry")
            return _original_auto_wire(**kwargs)

        monkeypatch.setattr(app_module, "auto_wire_meetings", _capturing)

        create_app(
            config=root_config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=cost_tracker,
        )

        assert "agent_registry" in captured
        assert captured["agent_registry"] is not None


@pytest.mark.unit
class TestCreateAppEnvAutoWire:
    """Verify create_app auto-wires SQLite persistence from SYNTHORG_DB_PATH."""

    @pytest.mark.parametrize(
        ("env_value", "expect_persistence"),
        [
            (":memory:", True),
            (None, False),
        ],
        ids=["with_env_var", "without_env_var"],
    )
    def test_persistence_from_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        root_config: Any,
        env_value: str | None,
        expect_persistence: bool,
    ) -> None:
        """Persistence is auto-wired when env var is set, None otherwise."""
        if env_value is not None:
            monkeypatch.setenv("SYNTHORG_DB_PATH", env_value)
        else:
            monkeypatch.delenv("SYNTHORG_DB_PATH", raising=False)
        app = create_app(config=root_config)
        state = app.state["app_state"]
        assert state.has_persistence == expect_persistence


@pytest.mark.unit
class TestCreateAppCursorSecretGuard:
    """Verify create_app refuses to start without a stable cursor secret.

    The boot guard is unconditional -- dev, pre-release, and prod all
    share the same posture so the failure cannot hide behind a "looks
    fine in dev" code path. ``tests/conftest.py`` sets the env var via
    ``setdefault`` so other tests build the app normally; here we
    explicitly delete it to exercise the refusal branch.
    """

    def test_ephemeral_cursor_secret_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        root_config: Any,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_PAGINATION_CURSOR_SECRET", raising=False)
        with pytest.raises(
            RuntimeError,
            match="refusing to start with an ephemeral pagination cursor secret",
        ):
            create_app(config=root_config)


@pytest.mark.unit
class TestResolveArtifactDirEnv:
    """Lock down the SYNTHORG_ARTIFACT_DIR resolution helper.

    Postgres-mode auto-wire falls back to /data when the env var is
    unset or whitespace-only. A previous implementation used
    ``os.environ.get(...) or "/data"`` which treated a whitespace-only
    value as truthy and silently wrote artifacts into the process
    working directory instead; these tests are the guardrail.
    """

    def test_unset_env_falls_back_to_data(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_ARTIFACT_DIR", raising=False)
        assert _resolve_artifact_dir_env() == "/data"

    @pytest.mark.parametrize(
        "env_value",
        ["", " ", "   ", "\t", "\n", " \t \n "],
        ids=[
            "empty",
            "single_space",
            "multi_space",
            "tab",
            "newline",
            "mixed_whitespace",
        ],
    )
    def test_whitespace_only_env_falls_back_to_data(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_value: str,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_ARTIFACT_DIR", env_value)
        assert _resolve_artifact_dir_env() == "/data"

    def test_explicit_path_is_returned_as_is(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        absolute = str(tmp_path / "custom-artifacts")
        monkeypatch.setenv("SYNTHORG_ARTIFACT_DIR", absolute)
        assert _resolve_artifact_dir_env() == absolute

    def test_surrounding_whitespace_is_stripped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        absolute = str(tmp_path / "artifacts")
        monkeypatch.setenv("SYNTHORG_ARTIFACT_DIR", f"  {absolute}  ")
        assert _resolve_artifact_dir_env() == absolute

    @pytest.mark.parametrize(
        "relative_path",
        ["artifacts", "../artifacts", "./local"],
        ids=["bare", "traversal", "dot_prefix"],
    )
    def test_non_absolute_path_raises_value_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        relative_path: str,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_ARTIFACT_DIR", relative_path)
        with pytest.raises(ValueError, match="absolute"):
            _resolve_artifact_dir_env()


@pytest.mark.unit
class TestAppLifecycle:
    async def test_startup_partial_failure_cleanup(
        self,
        root_config: Any,
    ) -> None:
        """Persistence ok, bus fails → persistence cleaned up."""
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.lifecycle import _safe_startup
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
            await _safe_startup(
                persistence,
                bus,
                None,
                None,
                None,
                None,
                None,
                None,
                app_state,
            )
        # Persistence should have been disconnected during cleanup
        assert not persistence.is_connected

    async def test_shutdown_error_handling(self) -> None:
        """Shutdown errors are logged but don't propagate."""
        from synthorg.api.lifecycle import _safe_shutdown
        from tests.unit.api.conftest import FakePersistenceBackend

        persistence = FakePersistenceBackend()

        async def failing_disconnect() -> None:
            msg = "disconnect boom"
            raise RuntimeError(msg)

        persistence.disconnect = failing_disconnect

        # Should not raise even when disconnect fails
        await _safe_shutdown(None, None, None, None, None, None, None, persistence)

    async def test_task_engine_failure_cleans_up(
        self,
        root_config: Any,
    ) -> None:
        """Task engine start fails → persistence + bus cleaned up."""
        from unittest.mock import MagicMock

        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.lifecycle import _safe_startup
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
            await _safe_startup(
                persistence,
                bus,
                None,
                None,
                mock_te,
                None,
                None,
                None,
                app_state,
            )

        # Persistence and bus should be cleaned up
        assert not persistence.is_connected
        assert not bus.is_running

    async def test_settings_dispatcher_failure_cleans_up(
        self,
        root_config: Any,
    ) -> None:
        """Settings dispatcher start fails → persistence + bus cleaned up."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.lifecycle import _safe_startup
        from synthorg.api.state import AppState
        from tests.unit.api.conftest import (
            FakeMessageBus,
            FakePersistenceBackend,
        )

        persistence = FakePersistenceBackend()
        bus = FakeMessageBus()
        mock_dispatcher = MagicMock()
        mock_dispatcher.start = AsyncMock(
            side_effect=RuntimeError("dispatcher boom"),
        )
        mock_dispatcher.stop = AsyncMock()

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=persistence,
        )

        with pytest.raises(RuntimeError, match="dispatcher boom"):
            await _safe_startup(
                persistence,
                bus,
                None,
                mock_dispatcher,
                None,
                None,
                None,
                None,
                app_state,
            )

        # Persistence and bus should be cleaned up
        assert not persistence.is_connected
        assert not bus.is_running

    async def test_shutdown_task_engine_failure_does_not_propagate(self) -> None:
        """Task engine stop failure during shutdown is logged, not raised."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.api.lifecycle import _safe_shutdown

        mock_te = MagicMock()
        mock_te.stop = AsyncMock(side_effect=RuntimeError("stop boom"))

        # Should not raise even when task engine stop fails
        await _safe_shutdown(mock_te, None, None, None, None, None, None, None)

    async def test_meeting_scheduler_lifecycle(
        self,
        root_config: Any,
    ) -> None:
        """Meeting scheduler start/stop are called during lifecycle."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.lifecycle import _safe_shutdown, _safe_startup
        from synthorg.api.state import AppState
        from synthorg.communication.meeting.scheduler import (
            MeetingScheduler,
        )
        from tests.unit.api.conftest import (
            FakeMessageBus,
            FakePersistenceBackend,
        )

        persistence = FakePersistenceBackend()
        bus = FakeMessageBus()
        mock_sched = MagicMock(spec=MeetingScheduler)
        mock_sched.start = AsyncMock(spec=MeetingScheduler.start)
        mock_sched.stop = AsyncMock(spec=MeetingScheduler.stop)

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
            None,
            mock_sched,
            None,
            None,
            app_state,
        )
        mock_sched.start.assert_awaited_once()

        await _safe_shutdown(None, mock_sched, None, None, None, None, None, None)
        mock_sched.stop.assert_awaited_once()

    async def test_approval_timeout_scheduler_lifecycle(
        self,
        root_config: Any,
    ) -> None:
        """Approval timeout scheduler start/stop are called during lifecycle."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.lifecycle import _safe_shutdown, _safe_startup
        from synthorg.api.state import AppState
        from synthorg.security.timeout.scheduler import (
            ApprovalTimeoutScheduler,
        )
        from tests.unit.api.conftest import (
            FakeMessageBus,
            FakePersistenceBackend,
        )

        persistence = FakePersistenceBackend()
        bus = FakeMessageBus()
        mock_sched = MagicMock(spec=ApprovalTimeoutScheduler)
        mock_sched.start = AsyncMock(spec=ApprovalTimeoutScheduler.start)
        mock_sched.stop = AsyncMock(spec=ApprovalTimeoutScheduler.stop)

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
            None,
            None,
            None,
            mock_sched,
            app_state,
        )
        mock_sched.start.assert_awaited_once()

        await _safe_shutdown(None, None, None, mock_sched, None, None, None, None)
        mock_sched.stop.assert_awaited_once()


@pytest.mark.unit
class TestTryStop:
    """Tests for the _try_stop helper."""

    async def test_try_stop_success(self) -> None:
        """Successful coroutine runs without error."""
        from synthorg.api.lifecycle import _try_stop

        called = False

        async def noop() -> None:
            nonlocal called
            called = True

        await _try_stop(noop(), "event", "error msg")
        assert called is True

    async def test_try_stop_exception_swallowed(self) -> None:
        """Non-fatal exceptions are swallowed (logged)."""
        from synthorg.api.lifecycle import _try_stop

        async def fail() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        # Should not raise
        await _try_stop(fail(), "event", "error msg")

    async def test_try_stop_memory_error_reraises(self) -> None:
        """MemoryError is re-raised immediately."""
        from synthorg.api.lifecycle import _try_stop

        async def oom() -> None:
            raise MemoryError

        with pytest.raises(MemoryError):
            await _try_stop(oom(), "event", "error msg")

    async def test_try_stop_recursion_error_reraises(self) -> None:
        """RecursionError is re-raised immediately."""
        from synthorg.api.lifecycle import _try_stop

        async def recurse() -> None:
            raise RecursionError

        with pytest.raises(RecursionError):
            await _try_stop(recurse(), "event", "error msg")

    async def test_try_stop_timeout_logs_and_returns_false(self) -> None:
        """A service that exceeds its per-service budget logs TIMEOUT and
        swallows the error so sibling services still run."""
        import asyncio

        import structlog.testing

        from synthorg.api.lifecycle import _try_stop

        async def hang() -> None:
            # Wait indefinitely so the ``asyncio.wait_for`` deadline
            # hits; ``asyncio.Event().wait()`` is cancellation-safe and
            # carries no wall-clock dependency.
            await asyncio.Event().wait()

        with structlog.testing.capture_logs() as events:
            result = await _try_stop(
                hang(),
                "event",
                "error msg",
                timeout=0.05,
                service="unit-test-service",
            )
        assert result is False
        # The timeout path emits the explicit ``API_APP_SHUTDOWN_TIMEOUT``
        # event at ERROR with the service name so operators can
        # distinguish hangs from crashes.
        matched = [
            e
            for e in events
            if e.get("service") == "unit-test-service" and e.get("log_level") == "error"
        ]
        assert matched, f"expected timeout log; got {events}"
        assert matched[0].get("timeout_seconds") == 0.05


@pytest.mark.unit
class TestAutoWirePhase1:
    """Construction-time auto-wiring: services created at app build."""

    def test_auto_wires_message_bus(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Message bus is auto-wired when not provided."""
        monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
        app = create_app()
        state: AppState = app.state["app_state"]
        assert state.has_message_bus

    def test_auto_wired_message_bus_is_in_memory(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-wired message bus is InMemoryMessageBus."""
        monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
        app = create_app()
        state: AppState = app.state["app_state"]
        # Access the private field to check the concrete type.
        assert isinstance(state._message_bus, InMemoryMessageBus)

    def test_auto_wires_cost_tracker(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cost tracker is auto-wired when not provided."""
        monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
        app = create_app()
        state: AppState = app.state["app_state"]
        assert isinstance(state._cost_tracker, CostTracker)

    def test_auto_wires_task_engine(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Task engine is auto-wired when persistence is available."""
        monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
        app = create_app()
        state: AppState = app.state["app_state"]
        assert state.has_task_engine
        assert isinstance(state._task_engine, TaskEngine)

    def test_task_engine_not_wired_without_persistence(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Task engine is not auto-wired without persistence."""
        monkeypatch.delenv("SYNTHORG_DB_PATH", raising=False)
        app = create_app()
        state: AppState = app.state["app_state"]
        assert not state.has_task_engine

    def test_explicit_services_not_overridden(
        self,
        fake_persistence: Any,
        fake_message_bus: Any,
        cost_tracker: Any,
        root_config: Any,
        fake_task_engine: Any,
    ) -> None:
        """Explicitly provided services are not replaced by auto-wiring."""
        app = create_app(
            config=root_config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=cost_tracker,
            task_engine=fake_task_engine,
        )
        state: AppState = app.state["app_state"]
        assert state._message_bus is fake_message_bus
        assert state._cost_tracker is cost_tracker
        assert state._task_engine is fake_task_engine

    def test_no_persistence_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Warning is logged when no persistence is available."""
        monkeypatch.delenv("SYNTHORG_DB_PATH", raising=False)
        # Prevent bootstrap_logging from resetting structlog's
        # capture_logs context during create_app().
        monkeypatch.setattr(
            "synthorg.api.app._bootstrap_app_logging",
            lambda config: config,
        )
        import structlog

        with structlog.testing.capture_logs() as logs:
            create_app()
        warning_logs = [
            e
            for e in logs
            if e.get("log_level") == "warning"
            and "No persistence backend available" in str(e.get("note", ""))
        ]
        assert len(warning_logs) == 1


@pytest.mark.unit
class TestAutoWirePhase2:
    """On-startup auto-wiring: settings_service after persistence connects."""

    async def test_auto_wire_settings_creates_service(
        self,
        fake_persistence: Any,
        fake_message_bus: Any,
        root_config: Any,
    ) -> None:
        """auto_wire_settings creates SettingsService on AppState."""
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.auto_wire import auto_wire_settings
        from synthorg.api.lifecycle_helpers.settings_dispatcher import (
            _build_settings_dispatcher,
        )

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=fake_persistence,
            message_bus=fake_message_bus,
        )
        assert not app_state.has_settings_service

        dispatcher = await auto_wire_settings(
            fake_persistence,
            fake_message_bus,
            root_config,
            app_state,
            None,
            _build_settings_dispatcher,
        )

        assert app_state.has_settings_service
        assert app_state.has_config_resolver  # type: ignore[unreachable]
        assert app_state.has_provider_management
        # Dispatcher is created when bus is available
        assert dispatcher is not None

        # Clean up
        await dispatcher.stop()

    async def test_auto_wire_settings_without_bus(
        self,
        fake_persistence: Any,
        root_config: Any,
    ) -> None:
        """auto_wire_settings works without a message bus."""
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.auto_wire import auto_wire_settings
        from synthorg.api.lifecycle_helpers.settings_dispatcher import (
            _build_settings_dispatcher,
        )

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=fake_persistence,
        )
        dispatcher = await auto_wire_settings(
            fake_persistence,
            None,
            root_config,
            app_state,
            None,
            _build_settings_dispatcher,
        )

        assert app_state.has_settings_service
        # No dispatcher without a bus
        assert dispatcher is None

    def test_phase2_flag_set_when_no_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auto_wire_settings flag is True when settings_service not provided."""
        monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
        app = create_app()
        state: AppState = app.state["app_state"]
        # Before startup, settings not yet wired (on_startup pending).
        assert not state.has_settings_service
        # But construction-time services are wired.
        assert state.has_message_bus
        assert state.has_task_engine

    def test_phase2_skipped_when_settings_provided(
        self,
        fake_persistence: Any,
        fake_message_bus: Any,
        root_config: Any,
    ) -> None:
        """On-startup auto-wire skipped when settings_service is explicitly provided."""
        import synthorg.settings.definitions  # noqa: F401
        from synthorg.settings.registry import get_registry
        from synthorg.settings.service import SettingsService

        settings_svc = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
            config=root_config,
        )
        app = create_app(
            config=root_config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            settings_service=settings_svc,
        )
        state: AppState = app.state["app_state"]
        assert state.has_settings_service
        # Verify it's the same instance we passed
        assert state._settings_service is settings_svc


@pytest.mark.unit
class TestAppStateSetSettingsService:
    """Tests for AppState.set_settings_service()."""

    def test_creates_resolver_and_management(
        self,
        fake_persistence: Any,
        root_config: Any,
    ) -> None:
        """set_settings_service creates ConfigResolver + ProviderManagement."""
        import synthorg.settings.definitions  # noqa: F401
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.settings.registry import get_registry
        from synthorg.settings.service import SettingsService

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=fake_persistence,
        )
        assert not app_state.has_settings_service
        assert not app_state.has_config_resolver
        assert not app_state.has_provider_management

        settings_svc = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
            config=root_config,
        )
        app_state.set_settings_service(settings_svc)

        assert app_state.has_settings_service
        assert app_state.has_config_resolver  # type: ignore[unreachable]
        assert app_state.has_provider_management

    def test_raises_if_already_set(
        self,
        fake_persistence: Any,
        root_config: Any,
    ) -> None:
        """set_settings_service raises RuntimeError if called twice."""
        import synthorg.settings.definitions  # noqa: F401
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.settings.registry import get_registry
        from synthorg.settings.service import SettingsService

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=fake_persistence,
        )
        settings_svc = SettingsService(
            repository=fake_persistence.settings,
            registry=get_registry(),
            config=root_config,
        )
        app_state.set_settings_service(settings_svc)

        with pytest.raises(RuntimeError, match="already configured"):
            app_state.set_settings_service(settings_svc)


@pytest.mark.unit
class TestAutoWirePhase1Details:
    """Detailed construction-time auto-wiring tests for edge cases."""

    def test_auto_wired_bus_includes_api_channels(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-wired message bus includes API channels for bridge compat."""
        from synthorg.api.channels import ALL_CHANNELS

        monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
        app = create_app()
        state: AppState = app.state["app_state"]
        bus = state._message_bus
        assert isinstance(bus, InMemoryMessageBus)
        # The bus config should include all API channels
        for ch in ALL_CHANNELS:
            assert ch in bus._config.channels

    def test_provider_registry_not_created_without_providers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Provider registry is None when no providers configured."""
        monkeypatch.setenv("SYNTHORG_DB_PATH", ":memory:")
        app = create_app()
        state: AppState = app.state["app_state"]
        assert not state.has_provider_registry

    def test_provider_registry_param_not_overridden(
        self,
        root_config: Any,
    ) -> None:
        """Explicit provider_registry is preserved by auto-wiring."""
        from unittest.mock import MagicMock

        fake_registry = MagicMock()
        app = create_app(
            config=root_config,
            provider_registry=fake_registry,
        )
        state: AppState = app.state["app_state"]
        assert state._provider_registry is fake_registry


@pytest.mark.unit
class TestAutoWirePhase2ErrorPaths:
    """On-startup auto-wiring: error handling and rollback."""

    async def test_phase2_failure_triggers_safe_shutdown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        root_config: Any,
    ) -> None:
        """On-startup auto-wire failure calls _safe_shutdown for cleanup."""
        from unittest.mock import AsyncMock

        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.lifecycle_builder import _build_lifecycle
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
            message_bus=bus,
        )

        async def failing_auto_wire(*args: Any, **kwargs: Any) -> None:
            msg = "phase2 boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(
            "synthorg.api.auto_wire.auto_wire_settings",
            failing_auto_wire,
        )

        startup, _shutdown = _build_lifecycle(
            persistence,
            bus,
            None,
            None,
            None,
            None,
            None,
            None,
            app_state,
            should_auto_wire_settings=True,
            effective_config=root_config,
        )

        # Mock _safe_startup so on_startup gets past the early stage.
        safe_startup_mock = AsyncMock()
        monkeypatch.setattr(
            "synthorg.api.lifecycle_builder._safe_startup",
            safe_startup_mock,
        )

        with pytest.raises(RuntimeError, match="phase2 boom"):
            await startup[0]()

    async def test_auto_wired_dispatcher_stopped_on_shutdown(
        self,
        root_config: Any,
        fake_persistence: Any,
        fake_message_bus: Any,
    ) -> None:
        """Auto-wired dispatcher is stopped during on_shutdown."""
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.auto_wire import auto_wire_settings
        from synthorg.api.lifecycle_helpers.settings_dispatcher import (
            _build_settings_dispatcher,
        )

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=fake_persistence,
            message_bus=fake_message_bus,
        )

        dispatcher = await auto_wire_settings(
            fake_persistence,
            fake_message_bus,
            root_config,
            app_state,
            None,
            _build_settings_dispatcher,
        )
        assert dispatcher is not None

        # Directly test the dispatcher stop
        await dispatcher.stop()

    async def test_dispatcher_start_failure_preserves_app_state(
        self,
        fake_persistence: Any,
        root_config: Any,
    ) -> None:
        """Dispatcher start failure does not mutate app_state."""
        from unittest.mock import AsyncMock, MagicMock

        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.auto_wire import auto_wire_settings

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=fake_persistence,
        )

        def failing_builder(*args: Any, **kwargs: Any) -> MagicMock:
            mock_dispatcher = MagicMock()
            mock_dispatcher.start = AsyncMock(
                side_effect=RuntimeError("dispatcher start boom"),
            )
            mock_dispatcher.stop = AsyncMock()
            return mock_dispatcher

        with pytest.raises(RuntimeError, match="dispatcher start boom"):
            await auto_wire_settings(
                fake_persistence,
                None,
                root_config,
                app_state,
                None,
                failing_builder,
            )

        # AppState must not have been mutated
        assert not app_state.has_settings_service

    async def test_settings_service_creation_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_persistence: Any,
        root_config: Any,
    ) -> None:
        """SettingsService construction failure propagates cleanly."""
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.auto_wire import auto_wire_settings

        app_state = AppState(
            config=root_config,
            approval_store=ApprovalStore(),
            persistence=fake_persistence,
        )

        # Set an invalid encryption key to trigger SettingsService failure
        monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", "invalid-key")

        from synthorg.settings.errors import SettingsEncryptionError

        with pytest.raises(SettingsEncryptionError):
            await auto_wire_settings(
                fake_persistence,
                None,
                root_config,
                app_state,
                None,
                lambda *a, **kw: None,
            )

        # AppState must not have been mutated
        assert not app_state.has_settings_service


@pytest.mark.unit
class TestAutoWirePhase1ErrorPaths:
    """Construction-time auto-wiring: error handling edge cases."""

    def test_channel_overlap_deduplication(
        self,
        root_config: Any,
    ) -> None:
        """Channels already in bus config are not duplicated."""
        from synthorg.api.auto_wire import auto_wire_phase1
        from synthorg.api.channels import ALL_CHANNELS
        from synthorg.communication.bus.memory import InMemoryMessageBus

        # Add an API channel to the bus config so it overlaps
        bus_cfg = root_config.communication.message_bus
        overlap_channel = ALL_CHANNELS[0]
        if overlap_channel not in bus_cfg.channels:
            bus_cfg = bus_cfg.model_copy(
                update={"channels": (*bus_cfg.channels, overlap_channel)},
            )
            comm = root_config.communication.model_copy(
                update={"message_bus": bus_cfg},
            )
            config = root_config.model_copy(update={"communication": comm})
        else:
            config = root_config

        result = auto_wire_phase1(
            effective_config=config,
            persistence=None,
            message_bus=None,
            cost_tracker=None,
            task_engine=None,
            provider_registry=None,
            provider_health_tracker=None,
        )
        bus = result.message_bus
        assert isinstance(bus, InMemoryMessageBus)
        # Verify no duplicates
        channels = bus._config.channels
        assert len(channels) == len(set(channels))
        # All API channels present
        for ch in ALL_CHANNELS:
            assert ch in channels

    def test_cost_tracker_creation_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        root_config: Any,
    ) -> None:
        """CostTracker construction failure propagates from auto_wire_phase1."""
        from synthorg.api.auto_wire import auto_wire_phase1

        def failing_init(self: Any, **kwargs: Any) -> None:
            msg = "tracker boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(CostTracker, "__init__", failing_init)

        with pytest.raises(RuntimeError, match="tracker boom"):
            auto_wire_phase1(
                effective_config=root_config,
                persistence=None,
                message_bus=None,
                cost_tracker=None,
                task_engine=None,
                provider_registry=None,
                provider_health_tracker=None,
            )

    def test_message_bus_creation_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        root_config: Any,
    ) -> None:
        """InMemoryMessageBus construction failure propagates."""
        from synthorg.api.auto_wire import auto_wire_phase1
        from synthorg.communication.bus.memory import InMemoryMessageBus

        def failing_init(self: Any, **kwargs: Any) -> None:
            msg = "bus boom"
            raise RuntimeError(msg)

        monkeypatch.setattr(InMemoryMessageBus, "__init__", failing_init)

        with pytest.raises(RuntimeError, match="bus boom"):
            auto_wire_phase1(
                effective_config=root_config,
                persistence=None,
                message_bus=None,
                cost_tracker=None,
                task_engine=None,
                provider_registry=None,
                provider_health_tracker=None,
            )


@pytest.mark.unit
class TestBootstrapAppLogging:
    """Tests for _bootstrap_app_logging env var branches."""

    def test_no_log_dir_calls_bootstrap(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without SYNTHORG_LOG_DIR, bootstrap_logging is called unchanged."""
        monkeypatch.delenv("SYNTHORG_LOG_DIR", raising=False)
        calls: list[object] = []
        monkeypatch.setattr(
            "synthorg.config.bootstrap_logging",
            calls.append,
        )
        config = RootConfig(company_name="test-co")
        result = _bootstrap_app_logging(config)
        assert len(calls) == 1
        assert calls[0] is config
        assert result is config

    def test_log_dir_with_existing_logging_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SYNTHORG_LOG_DIR overrides log_dir in existing logging config."""
        monkeypatch.setenv("SYNTHORG_LOG_DIR", "/custom/logs")
        calls: list[RootConfig] = []
        monkeypatch.setattr(
            "synthorg.config.bootstrap_logging",
            calls.append,
        )
        config = RootConfig(
            company_name="test-co",
            logging=LogConfig(sinks=DEFAULT_SINKS, log_dir="original"),
        )
        result = _bootstrap_app_logging(config)
        assert len(calls) == 1
        assert calls[0].logging is not None
        assert calls[0].logging.log_dir == "/custom/logs"
        # Return value is the patched config, not the original.
        assert result is not config
        assert result.logging is not None
        assert result.logging.log_dir == "/custom/logs"

    def test_log_dir_without_logging_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SYNTHORG_LOG_DIR creates a LogConfig when none exists."""
        monkeypatch.setenv("SYNTHORG_LOG_DIR", "/data/logs")
        calls: list[RootConfig] = []
        monkeypatch.setattr(
            "synthorg.config.bootstrap_logging",
            calls.append,
        )
        config = RootConfig(company_name="test-co")
        assert config.logging is None
        result = _bootstrap_app_logging(config)
        assert len(calls) == 1
        assert calls[0].logging is not None
        assert calls[0].logging.log_dir == "/data/logs"
        assert len(calls[0].logging.sinks) == len(DEFAULT_SINKS)
        # Return value carries the new logging config.
        assert result.logging is not None
        assert result.logging.log_dir == "/data/logs"

    def test_whitespace_only_log_dir_treated_as_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Whitespace-only SYNTHORG_LOG_DIR behaves like unset."""
        monkeypatch.setenv("SYNTHORG_LOG_DIR", "   ")
        calls: list[object] = []
        monkeypatch.setattr(
            "synthorg.config.bootstrap_logging",
            calls.append,
        )
        config = RootConfig(company_name="test-co")
        result = _bootstrap_app_logging(config)
        assert len(calls) == 1
        assert calls[0] is config
        assert result is config

    def test_path_traversal_in_log_dir_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SYNTHORG_LOG_DIR with '..' raises ValueError."""
        monkeypatch.setenv("SYNTHORG_LOG_DIR", "../../etc")
        monkeypatch.setattr(
            "synthorg.config.bootstrap_logging",
            lambda _: None,
        )
        config = RootConfig(company_name="test-co")
        with pytest.raises(ValueError, match="path traversal"):
            _bootstrap_app_logging(config)

    def test_bootstrap_failure_prints_critical_and_reraises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """create_app re-raises and prints CRITICAL on logging failure."""
        monkeypatch.setattr(
            "synthorg.api.app._bootstrap_app_logging",
            _raise_runtime_error,
        )
        with pytest.raises(RuntimeError, match="boom"):
            create_app()
        captured = capsys.readouterr()
        assert "CRITICAL" in captured.err

    def test_patched_config_preserved_on_app_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """create_app stores the SYNTHORG_LOG_DIR-patched config on AppState."""
        monkeypatch.setenv("SYNTHORG_LOG_DIR", "/custom/volume/logs")
        # Prevent bootstrap_logging from actually reconfiguring structlog.
        monkeypatch.setattr(
            "synthorg.config.bootstrap_logging",
            lambda _config: None,
        )
        app = create_app()
        app_state = app.state["app_state"]
        assert app_state.config.logging is not None
        assert app_state.config.logging.log_dir == "/custom/volume/logs"


def _raise_runtime_error(_config: object) -> None:
    msg = "boom"
    raise RuntimeError(msg)


@pytest.mark.unit
class TestBuildMiddleware:
    """Tests for the tiered rate-limit middleware stack."""

    def test_middleware_stack_has_nine_entries(
        self,
        root_config: Any,
    ) -> None:
        from synthorg.api.auth.context import AuthContextMiddleware
        from synthorg.api.middleware_factory import _build_middleware

        mw = _build_middleware(root_config.api)
        # Nine layers (outside-in): ip_floor, ETagMiddleware,
        # auth_mw, AuthContextMiddleware, csrf_mw, unauth_rl,
        # RequestLoggingMiddleware, auth_rl, and the innermost
        # PerOpConcurrencyMiddleware.
        assert len(mw) == 9
        # Lock the position of AuthContextMiddleware: any middleware
        # after auth_mw that reads scope["user"] (CSRF, the rate-limit
        # tiers, request logging) depends on the per-task ContextVar
        # being already bound, so reordering this entry would silently
        # break get_authenticated_user_id() in those layers.
        assert isinstance(mw[3], AuthContextMiddleware)

    def test_three_rate_limiters_have_distinct_stores(
        self,
        root_config: Any,
    ) -> None:
        from litestar.middleware.rate_limit import (
            RateLimitConfig as LsRL,
        )

        from synthorg.api.middleware_factory import _build_middleware

        mw = _build_middleware(root_config.api)
        rl_configs: list[LsRL] = [
            entry.kwargs["config"]
            for entry in mw
            if hasattr(entry, "kwargs") and isinstance(entry.kwargs.get("config"), LsRL)
        ]
        stores = [cfg.store for cfg in rl_configs]
        assert stores == [
            "rate_limit_floor",
            "rate_limit_unauth",
            "rate_limit_auth",
        ]

    def test_middleware_ordering(
        self,
        root_config: Any,
    ) -> None:
        """floor → auth → unauth → auth-rl (with csrf + logging between)."""
        from litestar.middleware.rate_limit import (
            RateLimitConfig as LsRL,
        )

        from synthorg.api.middleware_factory import _build_middleware

        mw = _build_middleware(root_config.api)

        floor_idx: int | None = None
        unauth_idx: int | None = None
        auth_mw_idx: int | None = None
        auth_rl_idx: int | None = None

        for i, entry in enumerate(mw):
            cfg = (
                getattr(entry, "kwargs", {}).get("config")
                if hasattr(entry, "kwargs")
                else None
            )
            if isinstance(cfg, LsRL):
                # Match by explicit store name so the substring check
                # isn't ambiguous ("auth" is a suffix of "unauth").
                if cfg.store == "rate_limit_floor":
                    floor_idx = i
                elif cfg.store == "rate_limit_unauth":
                    unauth_idx = i
                elif cfg.store == "rate_limit_auth":
                    auth_rl_idx = i
            elif isinstance(entry, type) and "Auth" in entry.__name__:
                # Multiple middleware classes are in the stack
                # (auth, csrf, RequestLogging); pick the one whose
                # name contains "Auth".
                auth_mw_idx = i

        assert floor_idx is not None, "floor rate limiter not found"
        assert unauth_idx is not None, "unauth rate limiter not found"
        assert auth_mw_idx is not None, "auth middleware not found"
        assert auth_rl_idx is not None, "auth rate limiter not found"
        # Floor runs first so invalid-auth floods still hit a cap;
        # auth runs before the user-gated tiers so scope["user"] is
        # populated for check_throttle_handler.
        assert floor_idx < auth_mw_idx < unauth_idx < auth_rl_idx


@pytest.mark.unit
class TestAuthIdentifierForRequest:
    """Tests for _auth_identifier_for_request."""

    def test_returns_user_id_when_user_in_scope(self) -> None:
        from unittest.mock import MagicMock

        from litestar import Request

        from synthorg.api.middleware_factory import _auth_identifier_for_request
        from synthorg.core.auth.models import AuthenticatedUser

        request = MagicMock(spec=Request)
        user = MagicMock(spec=AuthenticatedUser)
        user.user_id = "user-abc-123"
        request.scope = {"user": user}
        assert _auth_identifier_for_request(request) == "user-abc-123"

    def test_falls_back_to_ip_when_no_user(self) -> None:
        from unittest.mock import MagicMock, patch

        from litestar import Request

        from synthorg.api.middleware_factory import _auth_identifier_for_request

        request = MagicMock(spec=Request)
        request.scope = {}
        with patch(
            "synthorg.api.middleware_factory.get_remote_address",
            return_value="10.0.0.1",
        ):
            assert _auth_identifier_for_request(request) == "10.0.0.1"

    def test_falls_back_to_ip_when_user_is_none(self) -> None:
        from unittest.mock import MagicMock, patch

        from litestar import Request

        from synthorg.api.middleware_factory import _auth_identifier_for_request

        request = MagicMock(spec=Request)
        request.scope = {"user": None}
        with patch(
            "synthorg.api.middleware_factory.get_remote_address",
            return_value="192.168.1.1",
        ):
            assert _auth_identifier_for_request(request) == "192.168.1.1"
