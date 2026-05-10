"""Tests for the ``lifecycle_cleanup_enabled`` live kill-switch.

When operators flip ``api.lifecycle_cleanup_enabled=false`` the WS
ticket / session / lockout cleanup loop must short-circuit every
tick without tearing down the task.  When ``True`` the loop must
call all three cleanup paths on every tick.

The per-tick driver below monkeypatches ``asyncio.sleep`` on the
lifecycle helpers module so the loop advances deterministically by
exactly N ticks; no wall-clock races.
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api import lifecycle_helpers


def _no_arg_sync() -> object:
    """Spec target for ``MagicMock(spec=...)`` substitutes that stand in
    for synchronous zero-arg cleanup methods (e.g.
    ``WsTicketStore.cleanup_expired``)."""
    return None


async def _no_arg_async() -> object:
    """Spec target for ``AsyncMock(spec=...)`` substitutes that stand in
    for async zero-arg cleanup methods."""
    return None


async def _config_get_async(_key: str, /) -> object:
    """Spec target for ``ConfigResolver.get_*`` substitutes."""
    return None


def _build_app_state(*, enabled: bool) -> SimpleNamespace:
    """Build a minimal ``AppState`` stand-in with counting stub stores."""
    ticket_store = SimpleNamespace(
        cleanup_expired=MagicMock(spec=_no_arg_sync, return_value=None),
    )
    session_store = SimpleNamespace(
        cleanup_expired=AsyncMock(spec=_no_arg_async, return_value=None),
    )
    lockout_store = SimpleNamespace(
        cleanup_expired=AsyncMock(spec=_no_arg_async, return_value=None),
    )
    config_resolver = SimpleNamespace(
        get_bool=AsyncMock(spec=_config_get_async, return_value=enabled),
        get_float=AsyncMock(spec=_config_get_async, return_value=0.001),
    )
    return SimpleNamespace(
        ticket_store=ticket_store,
        session_store=session_store,
        lockout_store=lockout_store,
        config_resolver=config_resolver,
        has_config_resolver=True,
        has_session_store=True,
        has_lockout_store=True,
        has_persistence=False,
    )


async def _run_loop_ticks(
    app_state: Any,
    ticks: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the cleanup loop for exactly *ticks* iterations, then cancel.

    Monkeypatches ``lifecycle_helpers.asyncio.sleep`` to a counting
    stub that yields control on each call and cancels the loop after
    the Nth sleep.  Cancellation is the loop's terminal state; the
    test observes side-effects on the stub stores.
    """
    real_sleep = asyncio.sleep
    remaining = ticks

    async def _deterministic_sleep(_: float) -> None:
        nonlocal remaining
        if remaining <= 0:
            raise asyncio.CancelledError
        remaining -= 1
        await real_sleep(0)

    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.asyncio.sleep",
        _deterministic_sleep,
    )
    task = asyncio.create_task(lifecycle_helpers._ticket_cleanup_loop(app_state))
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.unit
class TestLifecycleCleanupKillSwitch:
    """Flipping the setting gates all three cleanup paths together."""

    async def test_enabled_calls_all_cleanup_paths(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``enabled=True`` every tick runs all three cleanups exactly once."""
        app_state = _build_app_state(enabled=True)

        await _run_loop_ticks(app_state, ticks=2, monkeypatch=monkeypatch)

        assert app_state.ticket_store.cleanup_expired.call_count == 2
        assert app_state.session_store.cleanup_expired.await_count == 2
        assert app_state.lockout_store.cleanup_expired.await_count == 2
        # One resolver consult per tick -- the gate is live, not frozen.
        assert app_state.config_resolver.get_bool.await_count == 2

    async def test_disabled_short_circuits_every_tick(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``enabled=False`` no cleanup path runs on any tick.

        The resolver is consulted once per tick so the loop stays
        responsive to a live re-enable.
        """
        app_state = _build_app_state(enabled=False)

        await _run_loop_ticks(app_state, ticks=3, monkeypatch=monkeypatch)

        assert app_state.ticket_store.cleanup_expired.call_count == 0
        assert app_state.session_store.cleanup_expired.await_count == 0
        assert app_state.lockout_store.cleanup_expired.await_count == 0
        assert app_state.config_resolver.get_bool.await_count == 3


@pytest.mark.unit
class TestResolveLifecycleCleanupEnabled:
    """Fail-safe fallback policy for the resolver call itself."""

    async def test_no_resolver_returns_true(self) -> None:
        """Missing resolver keeps cleanup running (fail-safe)."""
        app_state = SimpleNamespace(has_config_resolver=False)

        assert (
            await lifecycle_helpers._resolve_lifecycle_cleanup_enabled(
                app_state,  # type: ignore[arg-type]
            )
            is True
        )

    async def test_resolver_exception_returns_true(self) -> None:
        """Resolver raising ``Exception`` keeps cleanup running (fail-safe)."""
        config_resolver = SimpleNamespace(
            get_bool=AsyncMock(
                spec=_config_get_async,
                side_effect=RuntimeError("settings backend down"),
            ),
        )
        app_state = SimpleNamespace(
            has_config_resolver=True,
            config_resolver=config_resolver,
        )

        assert (
            await lifecycle_helpers._resolve_lifecycle_cleanup_enabled(
                app_state,  # type: ignore[arg-type]
            )
            is True
        )


@pytest.mark.unit
class TestRunCleanupTickExceptionIsolation:
    """Each per-store cleanup failure is isolated from the others."""

    async def test_ticket_cleanup_failure_does_not_block_session_or_lockout(
        self,
    ) -> None:
        """``ticket_store.cleanup_expired`` raising still runs session + lockout."""
        ticket_store = SimpleNamespace(
            cleanup_expired=MagicMock(
                spec=_no_arg_sync, side_effect=RuntimeError("ticket exploded")
            ),
        )
        session_store = SimpleNamespace(
            cleanup_expired=AsyncMock(spec=_no_arg_async, return_value=None)
        )
        lockout_store = SimpleNamespace(
            cleanup_expired=AsyncMock(spec=_no_arg_async, return_value=None)
        )
        app_state = SimpleNamespace(
            ticket_store=ticket_store,
            session_store=session_store,
            lockout_store=lockout_store,
            has_session_store=True,
            has_lockout_store=True,
            has_persistence=False,
        )

        await lifecycle_helpers._run_cleanup_tick(app_state)  # type: ignore[arg-type]

        # Ticket raised -- but session and lockout still ran to completion.
        ticket_store.cleanup_expired.assert_called_once()
        session_store.cleanup_expired.assert_awaited_once()
        lockout_store.cleanup_expired.assert_awaited_once()

    async def test_session_cleanup_failure_does_not_block_lockout(self) -> None:
        """``session_store.cleanup_expired`` raising still runs lockout cleanup."""
        ticket_store = SimpleNamespace(
            cleanup_expired=MagicMock(spec=_no_arg_sync, return_value=None)
        )
        session_store = SimpleNamespace(
            cleanup_expired=AsyncMock(
                spec=_no_arg_async, side_effect=RuntimeError("sessions gone")
            ),
        )
        lockout_store = SimpleNamespace(
            cleanup_expired=AsyncMock(spec=_no_arg_async, return_value=None)
        )
        app_state = SimpleNamespace(
            ticket_store=ticket_store,
            session_store=session_store,
            lockout_store=lockout_store,
            has_session_store=True,
            has_lockout_store=True,
            has_persistence=False,
        )

        await lifecycle_helpers._run_cleanup_tick(app_state)  # type: ignore[arg-type]

        ticket_store.cleanup_expired.assert_called_once()
        session_store.cleanup_expired.assert_awaited_once()
        lockout_store.cleanup_expired.assert_awaited_once()

    async def test_memory_error_propagates_from_cleanup_tick(self) -> None:
        """``MemoryError`` escapes the cleanup tick -- OOM must not be swallowed."""
        ticket_store = SimpleNamespace(
            cleanup_expired=MagicMock(spec=_no_arg_sync, side_effect=MemoryError),
        )
        app_state = SimpleNamespace(
            ticket_store=ticket_store,
            has_session_store=False,
            has_lockout_store=False,
            has_persistence=False,
        )

        with pytest.raises(MemoryError):
            await lifecycle_helpers._run_cleanup_tick(app_state)  # type: ignore[arg-type]
