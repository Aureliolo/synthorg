"""Lifecycle wiring: OAuth-state cleanup runs alongside session/lockout.

The repository method ``OAuthStateRepository.cleanup_expired()`` deletes
rows whose ``expires_at`` has passed.  Without this wiring the
``oauth_states`` table grows unbounded -- states are short-lived
(minutes) and consumed once on callback, so abandoned flows accumulate
indefinitely until the next process restart.

These tests assert the wiring (the call site in ``_run_cleanup_tick``),
not the underlying delete behaviour (covered by the per-backend repo
tests under ``tests/unit/persistence/``).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api import lifecycle_helpers
from synthorg.api.auth.ticket_store import WsTicketStore
from synthorg.api.services.idempotency_service import IdempotencyService
from synthorg.persistence.connection_protocol import OAuthStateRepository

pytestmark = pytest.mark.unit


def _build_app_state(
    *,
    has_persistence: bool = True,
    oauth_cleanup_side_effect: type[BaseException] | None = None,
) -> SimpleNamespace:
    """Build a minimal ``AppState`` stand-in with stub stores + persistence."""
    ticket_store = MagicMock(spec=WsTicketStore)
    ticket_store.cleanup_expired.return_value = None
    if oauth_cleanup_side_effect is None:
        oauth_states = AsyncMock(spec=OAuthStateRepository)
        oauth_states.cleanup_expired.return_value = 0
    else:
        oauth_states = AsyncMock(spec=OAuthStateRepository)
        oauth_states.cleanup_expired.side_effect = oauth_cleanup_side_effect
    persistence = SimpleNamespace(oauth_states=oauth_states)
    idempotency_service = AsyncMock(spec=IdempotencyService)
    idempotency_service.cleanup_expired.return_value = None
    return SimpleNamespace(
        ticket_store=ticket_store,
        persistence=persistence,
        idempotency_service=idempotency_service,
        has_session_store=False,
        has_lockout_store=False,
        has_persistence=has_persistence,
        has_config_resolver=False,
    )


async def test_oauth_state_cleanup_invoked_when_persistence_present() -> None:
    """``_run_cleanup_tick`` calls ``oauth_states.cleanup_expired`` once."""
    app_state = _build_app_state(has_persistence=True)

    await lifecycle_helpers._run_cleanup_tick(app_state)  # type: ignore[arg-type]

    app_state.persistence.oauth_states.cleanup_expired.assert_awaited_once_with(600.0)


async def test_oauth_state_cleanup_skipped_without_persistence() -> None:
    """``has_persistence=False`` short-circuits before the OAuth call."""
    app_state = _build_app_state(has_persistence=False)

    await lifecycle_helpers._run_cleanup_tick(app_state)  # type: ignore[arg-type]

    app_state.persistence.oauth_states.cleanup_expired.assert_not_awaited()


async def test_oauth_state_cleanup_failure_does_not_block_idempotency() -> None:
    """A raise in OAuth cleanup must not skip the idempotency sweep."""

    class _OAuthBoomError(RuntimeError):
        pass

    app_state = _build_app_state(
        has_persistence=True,
        oauth_cleanup_side_effect=_OAuthBoomError,
    )

    await lifecycle_helpers._run_cleanup_tick(app_state)  # type: ignore[arg-type]

    app_state.persistence.oauth_states.cleanup_expired.assert_awaited_once()
    app_state.idempotency_service.cleanup_expired.assert_awaited_once()


async def test_oauth_state_cleanup_memory_error_propagates() -> None:
    """``MemoryError`` escapes the cleanup tick rather than being swallowed."""
    app_state = _build_app_state(
        has_persistence=True,
        oauth_cleanup_side_effect=MemoryError,
    )

    with pytest.raises(MemoryError):
        await lifecycle_helpers._run_cleanup_tick(app_state)  # type: ignore[arg-type]


async def test_oauth_state_cleanup_cancellation_propagates() -> None:
    """``asyncio.CancelledError`` must propagate so loop cancellation works."""
    app_state = _build_app_state(
        has_persistence=True,
        oauth_cleanup_side_effect=asyncio.CancelledError,
    )

    with pytest.raises(asyncio.CancelledError):
        await lifecycle_helpers._run_cleanup_tick(app_state)  # type: ignore[arg-type]
