"""Unit tests for the POSIX shutdown signal handler."""

import asyncio
import signal
import sys
from unittest.mock import patch

import pytest

from synthorg.api.signals import (
    _make_handler,
    _on_signal,
    install_shutdown_handlers,
)
from synthorg.api.state import AppState
from synthorg.engine.shutdown import ShutdownManager
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _fake_app_state() -> AppState:
    """AppState double exposing a real shutdown event + manager."""
    app_state: AppState = mock_of[AppState](
        shutdown_requested=asyncio.Event(),
        shutdown_manager=ShutdownManager(),
    )
    return app_state


class TestInstallShutdownHandlers:
    """``install_shutdown_handlers`` idempotency + platform detection."""

    async def test_resets_shutdown_event_on_reinstall(self) -> None:
        """A second install must clear a previously-set shutdown event."""
        app_state = _fake_app_state()
        app_state.shutdown_requested.set()
        assert app_state.shutdown_requested.is_set()
        install_shutdown_handlers(app_state)
        assert not app_state.shutdown_requested.is_set()

    async def test_idempotent_registration(self) -> None:
        """Calling twice on the same AppState must not raise."""
        app_state = _fake_app_state()
        install_shutdown_handlers(app_state)
        install_shutdown_handlers(app_state)

    async def test_skips_on_windows(self) -> None:
        """On Windows we fall back to uvicorn's handler and skip."""
        app_state = _fake_app_state()
        with patch("synthorg.api.signals.sys") as mock_sys:
            mock_sys.platform = "win32"
            install_shutdown_handlers(app_state)
            # Event is still reset before the platform check.
            assert not app_state.shutdown_requested.is_set()

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only code path",
    )
    async def test_survives_add_signal_handler_not_implemented(self) -> None:
        """Proactor loops raising NotImplementedError are logged + ignored."""
        app_state = _fake_app_state()
        loop = asyncio.get_running_loop()
        with patch.object(loop, "add_signal_handler", side_effect=NotImplementedError):
            # Must not raise; the skip is logged at DEBUG instead.
            install_shutdown_handlers(app_state)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only code path",
    )
    async def test_survives_add_signal_handler_value_error(self) -> None:
        """Non-main-thread lifespans (TestClient portal) are tolerated.

        Litestar's ``TestClient`` drives lifespan startup through an
        anyio portal running on a worker thread; ``add_signal_handler``
        bottoms out in ``signal.set_wakeup_fd`` which raises
        ``ValueError: set_wakeup_fd only works in main thread of the
        main interpreter``.  The helper must catch that and skip
        registration, since uvicorn in production owns signals and the
        TestClient lifespan does not need them.
        """
        app_state = _fake_app_state()
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "add_signal_handler",
            side_effect=ValueError(
                "set_wakeup_fd only works in main thread of the main interpreter",
            ),
        ):
            install_shutdown_handlers(app_state)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only code path",
    )
    async def test_survives_add_signal_handler_runtime_error(self) -> None:
        """Closed-loop or loop-state refusal is degraded, not fatal."""
        app_state = _fake_app_state()
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "add_signal_handler",
            side_effect=RuntimeError("loop is closed"),
        ):
            install_shutdown_handlers(app_state)


class TestOnSignal:
    """Handler behaviour when a signal arrives."""

    def test_sets_shutdown_flag(self) -> None:
        app_state = _fake_app_state()
        assert not app_state.shutdown_requested.is_set()
        _on_signal(signal.SIGTERM, app_state)
        assert app_state.shutdown_requested.is_set()

    def test_idempotent_set(self) -> None:
        """Double-signal is a no-op for already-set events."""
        app_state = _fake_app_state()
        _on_signal(signal.SIGTERM, app_state)
        _on_signal(signal.SIGTERM, app_state)
        assert app_state.shutdown_requested.is_set()

    def test_closes_cooperative_drain_gate(self) -> None:
        # The signal must also close the cooperative drain gate so the
        # coordinator rejects new parallel agent tasks immediately,
        # before the on-shutdown hook runs the bounded drain.
        app_state = _fake_app_state()
        assert not app_state.shutdown_manager.is_shutting_down()
        _on_signal(signal.SIGTERM, app_state)
        assert app_state.shutdown_manager.is_shutting_down()


class TestMakeHandler:
    """The closure factory captures sig + state correctly."""

    def test_closure_binds_signal_and_state(self) -> None:
        app_state = _fake_app_state()
        handler = _make_handler(signal.SIGINT, app_state)
        assert callable(handler)
        handler()
        assert app_state.shutdown_requested.is_set()

    def test_handler_invocation_survives_event_loop_mismatch(self) -> None:
        """Handler must work even when called outside an event loop."""
        app_state = _fake_app_state()
        handler = _make_handler(signal.SIGTERM, app_state)
        # Calls with no running loop should not explode.
        handler()
        assert app_state.shutdown_requested.is_set()
