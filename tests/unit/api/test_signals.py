"""Unit tests for the POSIX + win32 shutdown signal handlers."""

import asyncio
import signal
import sys
import threading
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from synthorg.api.signals import (
    _install_win32_handlers,
    _make_handler,
    _make_win32_handler,
    _on_signal,
    install_shutdown_handlers,
)
from synthorg.api.state import AppState
from synthorg.engine.shutdown import ShutdownManager
from tests._shared import mock_of

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_signal_handlers() -> Iterator[None]:
    """Save / restore SIGTERM + SIGINT so a real install cannot leak.

    The platform-agnostic tests call ``install_shutdown_handlers`` on the
    host platform; on win32 (main thread) that now registers real
    ``signal.signal`` handlers. Snapshot and restore them around every
    test so a worker's signal disposition is unchanged afterwards.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for sig, handler in saved.items():
            if handler is not None:
                signal.signal(sig, handler)


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


class TestWin32Handlers:
    """The win32 ``signal.signal`` fallback path."""

    async def test_registers_signal_signal_on_win32_main_thread(self) -> None:
        """On win32 main-thread the fallback registers both signals."""
        app_state = _fake_app_state()
        loop = asyncio.get_running_loop()
        with (
            patch("synthorg.api.signals.signal.signal") as mock_signal,
            patch(
                "synthorg.api.signals.threading.current_thread",
                return_value=threading.main_thread(),
            ),
        ):
            _install_win32_handlers(loop, app_state)
        registered = {call.args[0] for call in mock_signal.call_args_list}
        assert registered == {signal.SIGTERM, signal.SIGINT}

    async def test_skips_on_non_main_thread(self) -> None:
        """A worker-thread lifespan installs nothing (uvicorn owns it)."""
        app_state = _fake_app_state()
        loop = asyncio.get_running_loop()
        sentinel_thread = threading.Thread(target=lambda: None)
        with (
            patch("synthorg.api.signals.signal.signal") as mock_signal,
            patch(
                "synthorg.api.signals.threading.current_thread",
                return_value=sentinel_thread,
            ),
        ):
            _install_win32_handlers(loop, app_state)
        mock_signal.assert_not_called()

    async def test_win32_handler_flags_shutdown_via_loop(self) -> None:
        """The 2-arg C handler re-enters the loop and flags shutdown."""
        app_state = _fake_app_state()
        loop = asyncio.get_running_loop()
        handler = _make_win32_handler(signal.SIGTERM, app_state, loop)
        handler(int(signal.SIGTERM), None)
        # call_soon_threadsafe defers to the loop; let it run.
        await asyncio.sleep(0)
        assert app_state.shutdown_requested.is_set()


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
