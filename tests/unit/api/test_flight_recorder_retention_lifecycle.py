"""Unit tests for the flight-recorder retention purge loop helpers.

Covers ``_resolve_retention_days`` / ``_resolve_loop_enabled``
(resolver-available, resolver-missing, resolver-error, cancellation) and
``_retention_tick`` (happy path, missing-persistence no-op,
repository-error swallow).
"""

import asyncio
from collections.abc import Iterator
from datetime import datetime

import pytest
from typeguard import suppress_type_checks

from synthorg.api.lifecycle_helpers.flight_recorder_retention import (
    _resolve_loop_enabled,
    _resolve_retention_days,
    _retention_tick,
)
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from tests._shared import make_app_state
from tests.unit.api.fakes import FakePersistenceBackend

_NS = "cockpit"
_DAYS_KEY = "flight_recorder_retention_days"
_ENABLED_KEY = "flight_recorder_retention_loop_enabled"


@pytest.fixture(autouse=True)
def _suppress_typeguard() -> Iterator[None]:
    with suppress_type_checks():
        yield


class _FakeConfigResolver:
    def __init__(
        self,
        *,
        ints: dict[tuple[str, str], int] | None = None,
        bools: dict[tuple[str, str], bool] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._ints = ints or {}
        self._bools = bools or {}
        self._raise_exc = raise_exc

    async def get_int(self, namespace: str, key: str) -> int:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._ints[(namespace, key)]

    async def get_bool(self, namespace: str, key: str) -> bool:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._bools[(namespace, key)]


async def _state(
    *,
    persistence: FakePersistenceBackend | None = None,
    resolver: _FakeConfigResolver | None = None,
) -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test-company"),
        persistence=persistence,
        config_resolver=resolver,
    )


@pytest.mark.unit
class TestResolveRetentionDays:
    async def test_default_when_no_resolver(self) -> None:
        assert await _resolve_retention_days(await _state()) == 90

    async def test_reads_resolved_value(self) -> None:
        resolver = _FakeConfigResolver(ints={(_NS, _DAYS_KEY): 30})
        assert await _resolve_retention_days(await _state(resolver=resolver)) == 30

    async def test_falls_back_on_resolver_error(self) -> None:
        resolver = _FakeConfigResolver(raise_exc=RuntimeError("down"))
        assert await _resolve_retention_days(await _state(resolver=resolver)) == 90

    async def test_negative_reverts_to_fallback(self) -> None:
        resolver = _FakeConfigResolver(ints={(_NS, _DAYS_KEY): -1})
        assert await _resolve_retention_days(await _state(resolver=resolver)) == 90

    async def test_cancellation_propagates(self) -> None:
        resolver = _FakeConfigResolver(raise_exc=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await _resolve_retention_days(await _state(resolver=resolver))


@pytest.mark.unit
class TestResolveLoopEnabled:
    async def test_default_when_no_resolver(self) -> None:
        assert await _resolve_loop_enabled(await _state()) is True

    async def test_reads_resolved_value(self) -> None:
        resolver = _FakeConfigResolver(bools={(_NS, _ENABLED_KEY): False})
        assert await _resolve_loop_enabled(await _state(resolver=resolver)) is False

    async def test_falls_back_on_resolver_error(self) -> None:
        resolver = _FakeConfigResolver(raise_exc=RuntimeError("down"))
        assert await _resolve_loop_enabled(await _state(resolver=resolver)) is True


@pytest.mark.unit
class TestRetentionTick:
    async def test_missing_persistence_is_noop(self) -> None:
        resolver = _FakeConfigResolver(ints={(_NS, _DAYS_KEY): 30})
        await _retention_tick(await _state(resolver=resolver))

    async def test_invokes_purge(self) -> None:
        resolver = _FakeConfigResolver(ints={(_NS, _DAYS_KEY): 15})
        backend = FakePersistenceBackend()
        await backend.connect()
        state = await _state(persistence=backend, resolver=resolver)
        # No frames recorded: purge is a clean no-op that must not raise.
        await _retention_tick(state)

    async def test_repository_error_is_swallowed(self) -> None:
        resolver = _FakeConfigResolver(ints={(_NS, _DAYS_KEY): 15})
        backend = FakePersistenceBackend()
        await backend.connect()

        boom = RuntimeError("db down")

        async def _raise(_threshold: datetime) -> int:
            raise boom

        backend.flight_recorder_frames.purge_before = _raise  # type: ignore[assignment]
        state = await _state(persistence=backend, resolver=resolver)
        # Must not propagate -- the loop keeps running on the next tick.
        await _retention_tick(state)
