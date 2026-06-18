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
from collections.abc import Iterator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest
from typeguard import suppress_type_checks

from synthorg.api.api_core_state import (
    ApiCoreStateSlice,
    lockout_store_of,
    session_store_of,
    ticket_store_of,
)
from synthorg.api.lifecycle_helpers import ticket_cleanup as lifecycle_helpers
from synthorg.api.state import AppState
from synthorg.settings.state import config_resolver_of
from tests._shared import make_app_state


@pytest.fixture(autouse=True)
def _suppress_typeguard_for_store_protocols() -> Iterator[None]:
    """Suppress typeguard module-wide for the cleanup kill-switch tests.

    Every test drives the cleanup loop with minimal ``cleanup_expired``-only
    store doubles and asserts via the instrumented ``*_store_of`` accessors.
    The session / lockout accessors return ``SessionRepository`` /
    ``LockoutRepository`` -- ``@runtime_checkable`` protocols that declare a
    ``_revoked: set[str]`` *data* attribute (plus the full repository surface)
    which a lightweight double cannot satisfy, and typeguard's ``check_protocol``
    (unlike ``isinstance``) does not skip mocks. These tests verify cleanup-loop
    behaviour, not store type conformance, so the runtime check is suppressed
    for the whole module.
    """
    with suppress_type_checks():
        yield


async def _no_arg_async() -> object:
    """Spec target for ``create_autospec(...)`` substitutes that stand in
    for async zero-arg cleanup methods (e.g.
    ``WsTicketStore.cleanup_expired``)."""
    return None


async def _config_get_async(_namespace: str, _key: str, /) -> object:
    """Spec target for ``ConfigResolver.get_*`` substitutes.

    Real signature is ``(namespace, key) -> value``. ``create_autospec``
    enforces the positional-arg count, so the stub must mirror both
    positional parameters or the loop's two-positional-arg call raises
    ``TypeError: too many positional arguments`` and the test fails.
    """
    return None


def _build_app_state(*, enabled: bool) -> AppState:
    """Build a minimal ``AppState`` stand-in with counting stub stores.

    Each cleanup callable is a ``create_autospec(..., spec_set=True)``
    stub built against a zero-arg signature target so call-time
    signature mismatches surface as test failures (matches the
    test-doubles ladder strictness used elsewhere).
    """
    ticket_store = SimpleNamespace(
        cleanup_expired=create_autospec(
            _no_arg_async,
            spec_set=True,
            return_value=None,
        ),
    )
    session_store = SimpleNamespace(
        cleanup_expired=create_autospec(
            _no_arg_async,
            spec_set=True,
            return_value=None,
        ),
    )
    lockout_store = SimpleNamespace(
        cleanup_expired=create_autospec(
            _no_arg_async,
            spec_set=True,
            return_value=None,
        ),
    )
    config_resolver = SimpleNamespace(
        get_bool=create_autospec(
            _config_get_async,
            spec_set=True,
            return_value=enabled,
        ),
        get_float=create_autospec(
            _config_get_async,
            spec_set=True,
            return_value=0.001,
        ),
    )
    return make_app_state(
        config_resolver=config_resolver,
        slices={
            ApiCoreStateSlice: {
                "ticket_store": ticket_store,
                "session_store": session_store,
                "lockout_store": lockout_store,
            },
        },
    )


async def _run_loop_ticks(
    app_state: AppState,
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
        "synthorg.api.lifecycle_helpers.ticket_cleanup.asyncio.sleep",
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

        assert (
            cast(AsyncMock, ticket_store_of(app_state).cleanup_expired).await_count == 2
        )
        assert (
            cast(AsyncMock, session_store_of(app_state).cleanup_expired).await_count
            == 2
        )
        assert (
            cast(AsyncMock, lockout_store_of(app_state).cleanup_expired).await_count
            == 2
        )
        # One resolver consult per tick -- the gate is live, not frozen.
        assert cast(AsyncMock, config_resolver_of(app_state).get_bool).await_count == 2

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

        assert (
            cast(AsyncMock, ticket_store_of(app_state).cleanup_expired).await_count == 0
        )
        assert (
            cast(AsyncMock, session_store_of(app_state).cleanup_expired).await_count
            == 0
        )
        assert (
            cast(AsyncMock, lockout_store_of(app_state).cleanup_expired).await_count
            == 0
        )
        assert cast(AsyncMock, config_resolver_of(app_state).get_bool).await_count == 3


@pytest.mark.unit
class TestResolveLifecycleCleanupEnabled:
    """Fail-safe fallback policy for the resolver call itself."""

    async def test_no_resolver_returns_true(self) -> None:
        """Missing resolver keeps cleanup running (fail-safe)."""
        app_state = make_app_state()

        assert (
            await lifecycle_helpers._resolve_lifecycle_cleanup_enabled(
                app_state,
            )
            is True
        )

    async def test_resolver_exception_returns_true(self) -> None:
        """Resolver raising ``Exception`` keeps cleanup running (fail-safe)."""
        config_resolver = SimpleNamespace(
            get_bool=create_autospec(
                _config_get_async,
                spec_set=True,
                side_effect=RuntimeError("settings backend down"),
            ),
        )
        app_state = make_app_state(config_resolver=config_resolver)

        assert (
            await lifecycle_helpers._resolve_lifecycle_cleanup_enabled(
                app_state,
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
            cleanup_expired=create_autospec(
                _no_arg_async,
                spec_set=True,
                side_effect=RuntimeError("ticket exploded"),
            ),
        )
        session_store = SimpleNamespace(
            cleanup_expired=create_autospec(
                _no_arg_async,
                spec_set=True,
                return_value=None,
            ),
        )
        lockout_store = SimpleNamespace(
            cleanup_expired=create_autospec(
                _no_arg_async,
                spec_set=True,
                return_value=None,
            ),
        )
        app_state = make_app_state(
            slices={
                ApiCoreStateSlice: {
                    "ticket_store": ticket_store,
                    "session_store": session_store,
                    "lockout_store": lockout_store,
                },
            },
        )

        await lifecycle_helpers._run_cleanup_tick(app_state)
        # Ticket raised -- but session and lockout still ran to completion.
        ticket_store.cleanup_expired.assert_awaited_once()
        session_store.cleanup_expired.assert_awaited_once()
        lockout_store.cleanup_expired.assert_awaited_once()

    async def test_session_cleanup_failure_does_not_block_lockout(self) -> None:
        """``session_store.cleanup_expired`` raising still runs lockout cleanup."""
        ticket_store = SimpleNamespace(
            cleanup_expired=create_autospec(
                _no_arg_async,
                spec_set=True,
                return_value=None,
            ),
        )
        session_store = SimpleNamespace(
            cleanup_expired=create_autospec(
                _no_arg_async,
                spec_set=True,
                side_effect=RuntimeError("sessions gone"),
            ),
        )
        lockout_store = SimpleNamespace(
            cleanup_expired=create_autospec(
                _no_arg_async,
                spec_set=True,
                return_value=None,
            ),
        )
        app_state = make_app_state(
            slices={
                ApiCoreStateSlice: {
                    "ticket_store": ticket_store,
                    "session_store": session_store,
                    "lockout_store": lockout_store,
                },
            },
        )

        await lifecycle_helpers._run_cleanup_tick(app_state)
        ticket_store.cleanup_expired.assert_awaited_once()
        session_store.cleanup_expired.assert_awaited_once()
        lockout_store.cleanup_expired.assert_awaited_once()

    async def test_memory_error_propagates_from_cleanup_tick(self) -> None:
        """``MemoryError`` escapes the cleanup tick -- OOM must not be swallowed."""
        ticket_store = SimpleNamespace(
            cleanup_expired=create_autospec(
                _no_arg_async,
                spec_set=True,
                side_effect=MemoryError,
            ),
        )
        app_state = make_app_state(
            slices={ApiCoreStateSlice: {"ticket_store": ticket_store}},
        )

        with pytest.raises(MemoryError):
            await lifecycle_helpers._run_cleanup_tick(app_state)
