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
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.api_core_state import ApiCoreStateSlice, idempotency_service_of
from synthorg.api.auth.ticket_store import WsTicketStore
from synthorg.api.lifecycle_helpers import ticket_cleanup as lifecycle_helpers
from synthorg.api.state import AppState
from synthorg.idempotency import IdempotencyService
from synthorg.persistence.connection_protocol import OAuthStateRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import persistence_of
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _build_app_state(
    *,
    has_persistence: bool = True,
    oauth_cleanup_side_effect: type[BaseException] | None = None,
) -> AppState:
    """Build a minimal ``AppState`` stand-in with stub stores + persistence."""
    ticket_store = MagicMock(spec=WsTicketStore)
    ticket_store.cleanup_expired.return_value = None
    if oauth_cleanup_side_effect is None:
        oauth_states = AsyncMock(spec=OAuthStateRepository)
        oauth_states.cleanup_expired.return_value = 0
    else:
        oauth_states = AsyncMock(spec=OAuthStateRepository)
        oauth_states.cleanup_expired.side_effect = oauth_cleanup_side_effect
    persistence = mock_of[PersistenceBackend](oauth_states=oauth_states)
    idempotency_service = AsyncMock(spec=IdempotencyService)
    idempotency_service.cleanup_expired.return_value = None
    return make_app_state(
        persistence=persistence if has_persistence else None,
        slices={
            ApiCoreStateSlice: {
                "ticket_store": ticket_store,
                "idempotency_service": idempotency_service,
            },
        },
    )


async def test_oauth_state_cleanup_invoked_when_persistence_present() -> None:
    """``_run_cleanup_tick`` calls ``oauth_states.cleanup_expired`` once."""
    app_state = _build_app_state(has_persistence=True)

    await lifecycle_helpers._run_cleanup_tick(app_state)
    cast(
        AsyncMock, persistence_of(app_state).oauth_states.cleanup_expired
    ).assert_awaited_once_with(600.0)


async def test_oauth_state_cleanup_skipped_without_persistence() -> None:
    """``has_persistence=False`` short-circuits before the OAuth call."""
    app_state = _build_app_state(has_persistence=False)

    # No persistence backend: the sweep returns at the
    # ``slice(PersistenceStateSlice).backend is None`` guard before any
    # OAuth-state repo access (the repo is unwired without a backend).
    await lifecycle_helpers._run_cleanup_tick(app_state)


async def test_oauth_state_cleanup_failure_does_not_block_idempotency() -> None:
    """A raise in OAuth cleanup must not skip the idempotency sweep."""

    class _OAuthBoomError(RuntimeError):
        pass

    app_state = _build_app_state(
        has_persistence=True,
        oauth_cleanup_side_effect=_OAuthBoomError,
    )

    await lifecycle_helpers._run_cleanup_tick(app_state)
    cast(
        AsyncMock, persistence_of(app_state).oauth_states.cleanup_expired
    ).assert_awaited_once()
    cast(
        AsyncMock, idempotency_service_of(app_state).cleanup_expired
    ).assert_awaited_once()


async def test_oauth_state_cleanup_memory_error_propagates() -> None:
    """``MemoryError`` escapes the cleanup tick rather than being swallowed."""
    app_state = _build_app_state(
        has_persistence=True,
        oauth_cleanup_side_effect=MemoryError,
    )

    with pytest.raises(MemoryError):
        await lifecycle_helpers._run_cleanup_tick(app_state)


async def test_oauth_state_cleanup_cancellation_propagates() -> None:
    """``asyncio.CancelledError`` must propagate so loop cancellation works."""
    app_state = _build_app_state(
        has_persistence=True,
        oauth_cleanup_side_effect=asyncio.CancelledError,
    )

    with pytest.raises(asyncio.CancelledError):
        await lifecycle_helpers._run_cleanup_tick(app_state)
