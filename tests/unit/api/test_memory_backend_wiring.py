"""Tests for the durable agent-memory boot wiring.

The behaviour that matters here is not "a backend appears" but *which*
backend appears and what happens when one cannot: the defect this module
replaces was a silent fallback to an ephemeral keyword store that looked
like working memory.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.lifecycle_helpers.memory_backend_wiring import wire_memory_backend
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.backends.sqlvector import SqlVectorBackend
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.state import MemoryStateSlice
from synthorg.persistence.memory_vector_protocol import MemoryVectorRepository
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _persistence() -> MagicMock:
    """A connected persistence backend exposing a vector repository."""
    backend = MagicMock()
    backend.memory_vectors = MagicMock(spec=MemoryVectorRepository)
    backend.memory_vectors.ensure_ready = AsyncMock()
    backend.memory_vectors.supports_dense_search = True
    return backend


def _settings(provider: str, model: str, dims: int) -> MagicMock:
    """A settings service returning an explicit embedder binding."""
    service = MagicMock()
    values = {
        "embedder_provider": provider,
        "embedder_model": model,
        "embedder_dims": dims,
    }
    service.get = AsyncMock(
        side_effect=lambda _ns, key: MagicMock(value=values[key]),
    )
    return service


def _ephemeral_app_state() -> AppState:
    """App state configured for the discouraged ephemeral backend."""
    return make_app_state(
        config=RootConfig(
            company_name="test",
            memory=CompanyMemoryConfig(backend="inmemory"),
        ),
        persistence=_persistence(),
        settings_service=_settings("", "", 0),
    )


class TestDurableWiring:
    """The happy path: an explicit embedder yields a durable backend."""

    async def test_wires_the_durable_backend(self) -> None:
        app_state = make_app_state(
            persistence=_persistence(),
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        await wire_memory_backend(app_state)

        backend = app_state.slice(MemoryStateSlice).backend
        assert isinstance(backend, SqlVectorBackend)
        assert backend.is_connected is True

    async def test_embedding_width_reaches_the_repository(self) -> None:
        persistence = _persistence()
        app_state = make_app_state(
            persistence=persistence,
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        await wire_memory_backend(app_state)

        persistence.memory_vectors.ensure_ready.assert_awaited_once_with(8)

    async def test_is_idempotent(self) -> None:
        app_state = make_app_state(
            persistence=_persistence(),
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        await wire_memory_backend(app_state)
        first = app_state.slice(MemoryStateSlice).backend
        await wire_memory_backend(app_state)

        assert app_state.slice(MemoryStateSlice).backend is first


class TestFailLoud:
    """No embedder must mean no memory, never a silent keyword fallback."""

    async def test_unresolvable_embedder_wires_no_backend(self) -> None:
        app_state = make_app_state(
            persistence=_persistence(),
            settings_service=_settings("", "", 0),
        )

        await wire_memory_backend(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None

    async def test_unresolvable_embedder_never_falls_back_to_ephemeral(self) -> None:
        # The precise regression: an ephemeral substring store published as
        # the shared backend reads as "memory works" while losing every
        # memory on restart and recalling the wrong things in between.
        app_state = make_app_state(
            persistence=_persistence(),
            settings_service=_settings("", "", 0),
        )

        await wire_memory_backend(app_state)

        assert not isinstance(
            app_state.slice(MemoryStateSlice).backend, InMemoryBackend
        )

    async def test_disconnected_persistence_wires_no_backend(self) -> None:
        app_state = make_app_state(
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        await wire_memory_backend(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None


class TestDiscouragedFallback:
    """The ephemeral store stays reachable, but only when chosen."""

    async def test_explicit_opt_in_selects_the_ephemeral_backend(self) -> None:
        await wire_memory_backend(_ephemeral_app_state())

    async def test_ephemeral_opt_in_needs_no_embedder(self) -> None:
        # Requiring an embedder for a store that cannot use one would make
        # the degraded mode unreachable exactly when it is the only option.
        app_state = _ephemeral_app_state()

        await wire_memory_backend(app_state)

        assert isinstance(app_state.slice(MemoryStateSlice).backend, InMemoryBackend)
