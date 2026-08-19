"""Tests for the durable agent-memory boot wiring.

The behaviour that matters here is not "a backend appears" but *which*
backend appears and what happens when one cannot: the defect this module
replaces was a silent fallback to an ephemeral keyword store that looked
like working memory.
"""

from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from synthorg.api.lifecycle_helpers.memory_backend_wiring import wire_memory_backend
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.backends.sqlvector import SqlVectorBackend
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.consolidation.config import ConsolidationConfig
from synthorg.memory.enums import ConsolidationInterval
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_WIRE_FAILED,
    MEMORY_EMBEDDER_UNRESOLVED,
)
from synthorg.persistence.memory_vector_protocol import MemoryVectorRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.enums import AuthType
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _persistence() -> Any:  # type: ignore[explicit-any]  # mock ergonomics; see mock_of
    """A connected persistence backend exposing a vector repository."""
    repo = mock_of[MemoryVectorRepository](supports_dense_search=True)
    repo.ensure_ready = AsyncMock(spec=MemoryVectorRepository.ensure_ready)
    return mock_of[PersistenceBackend](memory_vectors=repo)


def _settings(provider: str, model: str, dims: int) -> Any:  # type: ignore[explicit-any]  # mock ergonomics; see mock_of
    """A settings service returning an explicit embedder binding.

    The model is a serialized MODEL_REF, matching the setting's type: the
    provider travels with the model so nothing downstream has to guess it.
    """
    bound = serialize_model_ref(ModelRef(provider=provider, model_id=model))
    ref = bound if provider or model else ""
    values = {
        "embedder_model": ref,
        "embedder_dims": dims,
    }
    return mock_of[SettingsService](
        get=AsyncMock(
            side_effect=lambda _ns, key: SimpleNamespace(value=values[key]),
        ),
    )


def _config_resolver(base_url: str | None = None) -> Any:  # type: ignore[explicit-any]  # mock ergonomics; see mock_of
    """A resolver holding the one provider these fixtures embed through.

    Wiring reads it to learn where that provider is reachable, so a state
    without one models a boot order the app does not have.
    """
    return mock_of[ConfigResolver](
        get_provider_configs=AsyncMock(
            return_value={
                "test-provider": ProviderConfig(
                    driver="scripted",
                    auth_type=AuthType.NONE,
                    base_url=base_url,
                )
            }
        ),
    )


def _app_state(**overrides: Any) -> AppState:  # type: ignore[explicit-any]  # mock ergonomics; see mock_of
    """App state carrying the provider configs every wiring pass reads.

    Returns:
        The composed state, with a provider resolver unless one is given.
    """
    overrides.setdefault("config_resolver", _config_resolver())
    return make_app_state(**overrides)


async def _declines(app_state: AppState) -> str:
    """Run a pass that must decline, and return the condition it named.

    Returns:
        The reason handed to the reconciler, which every caller then
        compares against the slice: one condition, two readers.
    """
    with pytest.raises(SubsystemDeclinedError) as raised:
        await wire_memory_backend(app_state)
    reason = raised.value.reason
    assert reason == app_state.slice(MemoryStateSlice).wiring_failure, (
        "the subsystem surface and the health dialog must quote the same "
        "condition; they disagreed for as long as declining meant returning"
    )
    return reason


def _ephemeral_app_state() -> AppState:
    """App state configured for the discouraged ephemeral backend."""
    return _app_state(
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
        app_state = _app_state(
            persistence=_persistence(),
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        await wire_memory_backend(app_state)

        backend = app_state.slice(MemoryStateSlice).backend
        assert isinstance(backend, SqlVectorBackend)
        assert backend.is_connected is True

    async def test_embedding_width_reaches_the_repository(self) -> None:
        persistence = _persistence()
        app_state = _app_state(
            persistence=persistence,
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        await wire_memory_backend(app_state)

        persistence.memory_vectors.ensure_ready.assert_awaited_once_with(8)

    async def test_is_idempotent(self) -> None:
        app_state = _app_state(
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
        app_state = _app_state(
            persistence=_persistence(),
            settings_service=_settings("", "", 0),
        )

        await _declines(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None

    async def test_unresolvable_embedder_never_falls_back_to_ephemeral(self) -> None:
        # The precise regression: an ephemeral substring store published as
        # the shared backend reads as "memory works" while losing every
        # memory on restart and recalling the wrong things in between.
        app_state = _app_state(
            persistence=_persistence(),
            settings_service=_settings("", "", 0),
        )

        await _declines(app_state)

        assert not isinstance(
            app_state.slice(MemoryStateSlice).backend, InMemoryBackend
        )

    async def test_disconnected_persistence_wires_no_backend(self) -> None:
        app_state = _app_state(
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        reason = await _declines(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None
        # The database, not the embedder: a pass declining on the store must
        # not send the operator to re-pick a model that resolved fine.
        assert "not connected" in reason

    async def test_a_probe_failure_wires_no_backend_and_reports_it(self) -> None:
        """The real boot path, driven through an actual probe failure.

        Every other test here either pins a width (skipping the probe) or
        leaves the model unset (short-circuiting before it), so the case
        that matters most, a chosen model that cannot be reached, was only
        ever exercised at the resolve layer.
        """
        app_state = _app_state(
            persistence=_persistence(),
            # Falsy dims leaves the width unpinned, so the probe really runs.
            settings_service=_settings("test-provider", "test-embed-001", 0),
        )

        async def _unreachable(**_kwargs: object) -> int:
            msg = "connection reset"
            raise MemoryEmbeddingError(msg)

        with (
            mock.patch(
                "synthorg.memory.embedding.resolve.probe_embedder_dims",
                _unreachable,
            ),
            capture_logs() as logs,
        ):
            await _declines(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None
        unresolved = [e for e in logs if e["event"] == MEMORY_EMBEDDER_UNRESOLVED]
        assert unresolved, "an operator gets no other signal that memory is off"
        assert unresolved[-1]["log_level"] == "error"
        # The reason has to reach the slice, not just the log: the health
        # surface reads it, and without it an operator who HAS chosen a model
        # is told to choose one. Redacted rather than raw, so it names the
        # failure class and the binding, never the upstream's own text.
        recorded = app_state.slice(MemoryStateSlice).wiring_failure
        assert recorded is not None
        assert "MemoryEmbeddingError" in recorded
        assert "test-embed-001" in recorded

    async def test_a_probe_failure_never_starts_the_builtin(self) -> None:
        """The user's hard constraint, at the boot path rather than in unit
        isolation: a model that cannot embed leaves memory off, and does not
        hand over to the lexical built-in."""
        app_state = _app_state(
            persistence=_persistence(),
            settings_service=_settings("test-provider", "test-embed-001", 0),
        )

        async def _unreachable(**_kwargs: object) -> int:
            msg = "connection reset"
            raise MemoryEmbeddingError(msg)

        with mock.patch(
            "synthorg.memory.embedding.resolve.probe_embedder_dims",
            _unreachable,
        ):
            await _declines(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None
        assert app_state.slice(MemoryStateSlice).embedder_ref is None

    async def test_a_settings_read_failure_wires_no_backend(self) -> None:
        """A settings outage at boot must not resolve to a default binding."""
        settings = mock_of[SettingsService](
            get=AsyncMock(side_effect=RuntimeError("settings backend down")),
        )
        app_state = _app_state(
            persistence=_persistence(),
            settings_service=settings,
        )

        await _declines(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None

    async def test_a_legacy_bare_model_value_is_refused(self) -> None:
        """An install predating the MODEL_REF change holds a bare model id.

        It names a model with no provider, which resolution refuses by name
        rather than completing on the operator's behalf.
        """
        settings = mock_of[SettingsService](
            get=AsyncMock(
                side_effect=lambda _ns, key: SimpleNamespace(
                    value={"embedder_model": "legacy-bare-id", "embedder_dims": 0}[key]
                ),
            ),
        )
        app_state = _app_state(
            persistence=_persistence(),
            settings_service=settings,
        )

        await _declines(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None

    async def test_a_connect_failure_wires_no_backend_and_reports_it(self) -> None:
        # A backend that builds but cannot connect (e.g. the vector index
        # cannot be prepared) must be reported and left unwired, never
        # published half-open.
        persistence = _persistence()
        persistence.memory_vectors.ensure_ready = AsyncMock(
            side_effect=RuntimeError("index build failed")
        )
        app_state = _app_state(
            persistence=persistence,
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        with capture_logs() as logs:
            await _declines(app_state)

        assert app_state.slice(MemoryStateSlice).backend is None
        failures = [e for e in logs if e["event"] == MEMORY_BACKEND_WIRE_FAILED]
        assert len(failures) == 1
        assert failures[0]["log_level"] == "error"
        # Named as a store fault. The embedder resolved fine here, so falling
        # back to the generic "choose an embedding model" advice would send
        # the operator to re-pick a model that is not the problem.
        recorded = app_state.slice(MemoryStateSlice).wiring_failure
        assert recorded is not None
        assert "refused the connection" in recorded

    async def test_a_fixed_embedder_clears_a_previous_failure(self) -> None:
        # An operator who fixes the embedder but hits an unrelated store fault
        # must not keep reading the embedder reason they already resolved.
        persistence = _persistence()
        persistence.memory_vectors.ensure_ready = AsyncMock(
            side_effect=RuntimeError("index build failed")
        )
        app_state = _app_state(
            persistence=persistence,
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )
        app_state.wire(MemoryStateSlice, wiring_failure="a stale embedder reason")

        reason = await _declines(app_state)

        assert "a stale embedder reason" not in reason


class TestConsolidationSchedulerWiring:
    """The maintenance driver must actually start, or memory grows forever."""

    async def test_scheduler_is_wired_when_an_interval_is_set(self) -> None:
        app_state = _app_state(
            persistence=_persistence(),
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        await wire_memory_backend(app_state)

        scheduler = app_state.slice(MemoryStateSlice).consolidation_scheduler
        assert scheduler is not None
        # Leave no background task running for the next test.
        await scheduler.stop()

    async def test_no_scheduler_when_the_interval_is_never(self) -> None:
        app_state = _app_state(
            config=RootConfig(
                company_name="test",
                memory=CompanyMemoryConfig(
                    consolidation=ConsolidationConfig(
                        interval=ConsolidationInterval.NEVER,
                    ),
                ),
            ),
            persistence=_persistence(),
            settings_service=_settings("test-provider", "test-embed-001", 8),
        )

        await wire_memory_backend(app_state)

        assert app_state.slice(MemoryStateSlice).consolidation_scheduler is None
        # The backend itself still wired; only maintenance is off.
        assert app_state.slice(MemoryStateSlice).backend is not None


class TestDiscouragedFallback:
    """The ephemeral store stays reachable, but only when chosen."""

    async def test_ephemeral_opt_in_needs_no_embedder(self) -> None:
        # Requiring an embedder for a store that cannot use one would make
        # the degraded mode unreachable exactly when it is the only option.
        app_state = _ephemeral_app_state()

        await wire_memory_backend(app_state)

        assert isinstance(app_state.slice(MemoryStateSlice).backend, InMemoryBackend)
