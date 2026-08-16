"""Durable provider latches: what the subsystem does when it cannot come up.

Every refusal to activate must name its own condition, because
``GET /subsystems`` is the only place an operator learns why a boot came up
with latches that forget.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.lifecycle_helpers.provider_latch_wiring import wire_provider_latches
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.health import ProviderOutcomeClass
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.providers.latch import LatchedFailure
from synthorg.providers.state import ProvidersStateSlice
from tests._shared import make_app_state, mock_of

_WIRING = "synthorg.api.lifecycle_helpers.provider_latch_wiring._build_repo"


class _HandlelessBackend:
    """A wired backend that answers every handle request with a raise.

    Stands in for an unregistered backend kind, and for one whose connection
    is not there to hand out.
    """

    kind = "sqlite"

    def get_db(self) -> object:
        msg = "no database handle on this backend"
        raise NotImplementedError(msg)


class _FakeLatchStore:
    """The stored latches, or a read that fails the way a real one would.

    Implements the whole ``ProviderLatchRepository`` protocol because
    typeguard checks a fake against every member, not the ones it is handed
    for.
    """

    def __init__(
        self,
        *,
        latches: tuple[LatchedFailure, ...] = (),
        unreadable: bool = False,
    ) -> None:
        self._latches = latches
        self._unreadable = unreadable
        self.purged_before: datetime | None = None

    async def save(self, entity: LatchedFailure, /) -> None:
        self._latches = (*self._latches, entity)

    async def get(self, entity_id: tuple[str, str], /) -> LatchedFailure | None:
        return next((row for row in self._latches if row.pair == entity_id), None)

    async def delete(self, entity_id: tuple[str, str], /) -> bool:
        remaining = tuple(row for row in self._latches if row.pair != entity_id)
        dropped = len(remaining) != len(self._latches)
        self._latches = remaining
        return dropped

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[LatchedFailure, ...]:
        if self._unreadable:
            msg = "latch table unreadable"
            raise QueryError(msg)
        return self._latches[offset : offset + limit]

    async def purge_before(self, threshold: datetime, /) -> int:
        self.purged_before = threshold
        return 0


def _latch() -> LatchedFailure:
    return LatchedFailure(
        provider_name=NotBlankStr("test-provider"),
        model=NotBlankStr("example-basic-001"),
        outcome_class=ProviderOutcomeClass.PAYMENT_REQUIRED,
        occurred_at=datetime.now(UTC),
        error_message=NotBlankStr("balance is empty"),
        response_time_ms=12.0,
    )


def _use_store(monkeypatch: pytest.MonkeyPatch, store: _FakeLatchStore) -> None:
    monkeypatch.setattr(_WIRING, lambda _app_state: store)


@pytest.mark.unit
class TestProviderLatchWiring:
    async def test_declines_without_a_health_tracker(self) -> None:
        app_state = make_app_state(persistence=mock_of[PersistenceBackend]())
        with pytest.raises(SubsystemDeclinedError, match="health tracker"):
            await wire_provider_latches(app_state)

    async def test_declines_without_persistence(self) -> None:
        app_state = make_app_state(provider_health_tracker=ProviderHealthTracker())
        with pytest.raises(SubsystemDeclinedError, match="persistence backend"):
            await wire_provider_latches(app_state)

    async def test_declines_when_the_backend_hands_out_no_handle(self) -> None:
        # The reconciler must hear a condition it can report. A bare raise
        # here fails the whole pass, which is what turned one unavailable
        # store into a 500 on setup completion.
        app_state = make_app_state(
            persistence=_HandlelessBackend(),
            provider_health_tracker=ProviderHealthTracker(),
        )
        with pytest.raises(SubsystemDeclinedError, match="no database handle"):
            await wire_provider_latches(app_state)
        assert app_state.slice(ProvidersStateSlice).latch_store is None

    async def test_declines_when_the_stored_latches_cannot_be_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # "Unreadable" and "nothing latched" differ by the whole point of the
        # store, so the read failure must surface rather than restore zero.
        store = _FakeLatchStore(unreadable=True)
        _use_store(monkeypatch, store)
        app_state = make_app_state(
            persistence=_HandlelessBackend(),
            provider_health_tracker=ProviderHealthTracker(),
        )
        with pytest.raises(SubsystemDeclinedError, match="could not be read"):
            await wire_provider_latches(app_state)
        assert app_state.slice(ProvidersStateSlice).latch_store is None

    async def test_publishes_the_store_once_the_read_back_lands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeLatchStore(latches=(_latch(),))
        _use_store(monkeypatch, store)
        app_state = make_app_state(
            persistence=_HandlelessBackend(),
            provider_health_tracker=ProviderHealthTracker(),
        )
        await wire_provider_latches(app_state)
        assert app_state.slice(ProvidersStateSlice).latch_store is store

    async def test_is_idempotent_once_the_store_is_published(self) -> None:
        store = object()
        app_state = make_app_state()
        app_state.wire(ProvidersStateSlice, latch_store=store)
        await wire_provider_latches(app_state)
        assert app_state.slice(ProvidersStateSlice).latch_store is store
