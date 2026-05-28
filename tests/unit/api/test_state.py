"""Tests for the thin :class:`AppState` composition root.

After the feature-manifest collapse, ``AppState`` carries only
``config`` / ``clock`` / ``startup_time``, the cross-cutting mutable
primitives a frozen slice cannot own (the per-request-id lock registry,
bridge-config snapshots, WS timeouts, background-task sets, the
shutdown event), and a typed per-feature *slice store*. Every domain
service now lives on its feature slice and is read through
``app_state.slice(XStateSlice).field`` (or a ``*_of`` accessor), so the
per-service ``has_X`` flags and once-only ``set_X`` seams the old
god-object carried are gone.

These tests cover the slice store (``slice`` / ``has_slice`` /
``set_slice`` / ``swap_slice`` / ``wire``), the ``_require_service``
503 guard, the surviving primitives, the per-request-id lock registry,
and the API bridge-config snapshot accessor.
"""

import asyncio

import pytest
from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError

pytestmark = pytest.mark.unit


def _make_state() -> AppState:
    """Build a bare thin ``AppState`` with no services wired."""
    return AppState(config=RootConfig(company_name="test"))


class _ProbeSlice(BaseFeatureStateSlice):
    """Throwaway slice for exercising the generic slice-store seams."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first: object | None = None
    second: object | None = None


class TestSliceStore:
    """``slice`` / ``has_slice`` / ``set_slice`` / ``swap_slice`` / ``wire``."""

    def test_slice_composes_empty_when_absent(self) -> None:
        state = _make_state()
        composed = state.slice(_ProbeSlice)
        # A bare state lazily composes an empty slice so a reader never
        # faces an absent slice; the controller still 503s on a None field.
        assert isinstance(composed, _ProbeSlice)
        assert composed.first is None
        assert composed.second is None

    def test_slice_returns_same_instance_until_swapped(self) -> None:
        state = _make_state()
        first = state.slice(_ProbeSlice)
        assert state.slice(_ProbeSlice) is first

    def test_has_slice_reflects_composition(self) -> None:
        state = _make_state()
        assert state.has_slice(_ProbeSlice) is False
        state.slice(_ProbeSlice)  # lazy-composes the empty slice
        assert state.has_slice(_ProbeSlice) is True

    def test_set_slice_installs_once(self) -> None:
        state = _make_state()
        sentinel = object()
        state.set_slice(_ProbeSlice(first=sentinel))
        assert state.slice(_ProbeSlice).first is sentinel

    def test_set_slice_twice_raises(self) -> None:
        state = _make_state()
        state.set_slice(_ProbeSlice())
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_slice(_ProbeSlice())

    def test_swap_slice_replaces_existing(self) -> None:
        state = _make_state()
        first = _ProbeSlice(first=object())
        second = _ProbeSlice(first=object())
        state.set_slice(first)
        state.swap_slice(second)
        assert state.slice(_ProbeSlice) is second

    def test_swap_slice_is_atomic_whole_old_or_new(self) -> None:
        # A reader holding the old slice keeps its references; the next
        # ``slice`` call observes the whole new slice -- never a partial.
        state = _make_state()
        state.set_slice(_ProbeSlice(first="old"))
        held = state.slice(_ProbeSlice)
        state.swap_slice(_ProbeSlice(first="new"))
        assert held.first == "old"
        assert state.slice(_ProbeSlice).first == "new"

    def test_wire_updates_named_field(self) -> None:
        state = _make_state()
        sentinel = object()
        state.wire(_ProbeSlice, first=sentinel)
        assert state.slice(_ProbeSlice).first is sentinel
        assert state.slice(_ProbeSlice).second is None

    def test_wire_preserves_other_fields(self) -> None:
        state = _make_state()
        a = object()
        b = object()
        state.wire(_ProbeSlice, first=a)
        state.wire(_ProbeSlice, second=b)
        composed = state.slice(_ProbeSlice)
        assert composed.first is a
        assert composed.second is b

    def test_wire_yields_a_fresh_frozen_slice(self) -> None:
        state = _make_state()
        state.wire(_ProbeSlice, first=object())
        before = state.slice(_ProbeSlice)
        state.wire(_ProbeSlice, second=object())
        # ``model_copy(update=...)`` produces a new frozen instance, so a
        # reader holding the pre-wire slice is never mutated under it.
        assert state.slice(_ProbeSlice) is not before

    def test_set_field_once_installs(self) -> None:
        state = _make_state()
        sentinel = object()
        state.set_field_once(_ProbeSlice, "first", sentinel, "Probe")
        assert state.slice(_ProbeSlice).first is sentinel

    def test_set_field_once_twice_raises(self) -> None:
        state = _make_state()
        state.set_field_once(_ProbeSlice, "first", object(), "Probe")
        with pytest.raises(RuntimeError, match="Probe already configured"):
            state.set_field_once(_ProbeSlice, "first", object(), "Probe")

    def test_wire_if_field_absent_installs_when_absent(self) -> None:
        state = _make_state()
        sentinel = object()
        assert state.wire_if_field_absent(_ProbeSlice, "first", sentinel) is True
        assert state.slice(_ProbeSlice).first is sentinel

    def test_wire_if_field_absent_skips_when_present(self) -> None:
        state = _make_state()
        first = object()
        state.wire_if_field_absent(_ProbeSlice, "first", first)
        assert state.wire_if_field_absent(_ProbeSlice, "first", object()) is False
        # The first writer wins; a later if-absent call is a no-op.
        assert state.slice(_ProbeSlice).first is first

    def test_swap_field_returns_previous(self) -> None:
        state = _make_state()
        first = object()
        second = object()
        state.wire(_ProbeSlice, first=first)
        previous = state.swap_field_returning_previous(_ProbeSlice, "first", second)
        assert previous is first
        assert state.slice(_ProbeSlice).first is second

    def test_swap_field_returns_none_on_first_install(self) -> None:
        state = _make_state()
        previous = state.swap_field_returning_previous(_ProbeSlice, "first", object())
        assert previous is None

    def test_concurrent_set_field_once_single_winner(self) -> None:
        # The presence check and the write must share one lock
        # acquisition: with N threads racing on the same field, exactly
        # one installs and the rest see the field already set and raise.
        # A check outside the lock would let two threads both pass the
        # guard and both install (the once-only contract broken).
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        state = _make_state()
        workers = 16
        barrier = Barrier(workers)

        def _install(_: int) -> bool:
            barrier.wait()
            try:
                state.set_field_once(_ProbeSlice, "first", object(), "Probe")
            except RuntimeError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_install, range(workers)))
        assert sum(results) == 1, "set_field_once not atomic: multiple winners"
        assert state.slice(_ProbeSlice).first is not None

    def test_concurrent_wire_if_field_absent_single_winner(self) -> None:
        # Same race for the set-if-absent seam: only one of N concurrent
        # callers may report it installed the field; the rest observe
        # the winner's write under the shared lock and skip.
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        state = _make_state()
        workers = 16
        barrier = Barrier(workers)

        def _install(_: int) -> bool:
            barrier.wait()
            return state.wire_if_field_absent(_ProbeSlice, "first", object())

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_install, range(workers)))
        assert sum(results) == 1, "wire_if_field_absent not atomic: multiple winners"
        assert state.slice(_ProbeSlice).first is not None


class TestRequireService:
    """``_require_service`` returns the value or 503s on ``None``."""

    def test_returns_service_when_present(self) -> None:
        state = _make_state()
        sentinel = object()
        assert state._require_service(sentinel, "Probe") is sentinel

    def test_raises_service_unavailable_when_none(self) -> None:
        state = _make_state()
        with pytest.raises(
            ServiceUnavailableError,
            match="Probe Service not configured",
        ):
            state._require_service(None, "probe_service")


class TestPrimitives:
    """The cross-cutting mutable primitives a frozen slice cannot own."""

    def test_shutdown_requested_is_unset_event(self) -> None:
        state = _make_state()
        assert isinstance(state.shutdown_requested, asyncio.Event)
        assert not state.shutdown_requested.is_set()

    def test_bridge_config_applied_flips_once(self) -> None:
        state = _make_state()
        assert state.bridge_config_applied is False
        state.mark_bridge_config_applied()
        assert state.bridge_config_applied is True

    def test_background_task_sets_are_distinct(self) -> None:
        state = _make_state()
        assert state.objective_background_tasks == set()
        assert state.brownfield_background_tasks == set()
        assert state.objective_background_tasks is not state.brownfield_background_tasks

    def test_clock_and_startup_time_present(self) -> None:
        state = _make_state()
        assert state.clock is not None
        assert isinstance(state.startup_time, float)


@pytest.mark.unit
class TestAppStateRequestLocks:
    """Per-request-id ``asyncio.Lock`` registry lives on :class:`AppState`,
    not on a module global, so xdist workers and ``--count N`` repeat
    runs never inherit a Lock object bound to a closed event loop from
    a prior test. Each AppState owns its own dict; tests construct
    fresh AppStates, so isolation is automatic.
    """

    def test_lock_is_cached_per_request_id(self) -> None:
        state = _make_state()
        first = state.get_or_create_request_lock("req-1")
        second = state.get_or_create_request_lock("req-1")
        # Same id returns the same Lock instance; without identity the
        # ``async with`` ordering across two awaiters would not
        # serialise.
        assert first is second

    def test_locks_are_per_app_state(self) -> None:
        # Two AppStates must hold independent dicts so a leaked Lock
        # from one cannot poison the other (the precise xdist failure
        # mode this fix addresses).
        state_a = _make_state()
        state_b = _make_state()
        lock_a = state_a.get_or_create_request_lock("req-1")
        lock_b = state_b.get_or_create_request_lock("req-1")
        assert lock_a is not lock_b
        assert "req-1" in state_a._request_locks
        assert "req-1" in state_b._request_locks
        # Cross-state dicts are independent.
        assert state_a._request_locks is not state_b._request_locks

    def test_release_evicts_idle_lock(self) -> None:
        state = _make_state()
        lock = state.get_or_create_request_lock("req-1")
        assert "req-1" in state._request_locks
        # Lock is idle (never acquired), so release evicts.
        assert not lock.locked()
        state.release_request_lock_if_idle("req-1")
        assert "req-1" not in state._request_locks

    async def test_release_keeps_locked_entry(self) -> None:
        # Releasing while a waiter holds the lock would strand them on
        # an entry the next caller can no longer find. The helper must
        # only evict idle locks.
        state = _make_state()
        lock = state.get_or_create_request_lock("req-1")
        async with lock:
            state.release_request_lock_if_idle("req-1")
            assert "req-1" in state._request_locks

    def test_release_is_noop_for_unknown_id(self) -> None:
        state = _make_state()
        # No registry entry yet -- helper must not raise.
        state.release_request_lock_if_idle("never-seen")
        assert "never-seen" not in state._request_locks

    async def test_repeat_create_release_drains_clean(self) -> None:
        # Mirrors what ``--count 2`` exercises: two consecutive
        # create-release cycles on the same AppState should leave the
        # registry empty without cross-contamination.
        state = _make_state()
        for cycle in ("first", "second"):
            request_id = f"req-{cycle}"
            lock = state.get_or_create_request_lock(request_id)
            async with lock:
                pass
            state.release_request_lock_if_idle(request_id)
            assert request_id not in state._request_locks
        assert state._request_locks == {}

    def test_concurrent_create_returns_same_lock(self) -> None:
        # Two threads racing to create the same id must observe the
        # double-checked-locking guard: only one ``asyncio.Lock`` ever
        # lands in the registry, and both callers receive the same
        # identity. Without the inner re-check inside
        # ``_request_locks_guard`` two distinct Lock objects could be
        # returned, splitting the per-id serialisation guarantee that
        # the controller relies on.
        from concurrent.futures import ThreadPoolExecutor

        state = _make_state()

        def _create() -> object:
            return state.get_or_create_request_lock("req-race")

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: _create(), range(16)))
        first = results[0]
        assert all(r is first for r in results), (
            "DCL guard broken: concurrent create returned distinct Locks"
        )
        assert len(state._request_locks) == 1

    def test_eviction_caps_registry_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Defence-in-depth cap: a non-terminal request that scopes but
        # never advances would otherwise grow the dict forever (the
        # ``release_request_lock_if_idle`` path only fires on terminal
        # states). When the cap is hit, the oldest **idle** entries
        # are evicted; still-held entries are kept so an in-flight
        # approve/reject is never stranded on an evicted Lock. Tests
        # patch the cap to a small number so the assertion is
        # constant-time independent of the production ceiling.
        from synthorg.api import state_services_locks as _ss

        monkeypatch.setattr(_ss, "_MAX_REQUEST_LOCKS", 4)

        state = _make_state()
        for i in range(4):
            state.get_or_create_request_lock(f"prefill-{i}")
        assert len(state._request_locks) == 4
        # All prefilled locks are idle, so the eviction sweep should
        # bring the registry down to the cap when the next insert
        # arrives.
        state.get_or_create_request_lock("trigger-evict")
        assert len(state._request_locks) == 4
        # The newest insert must survive; one of the older idles was
        # evicted (FIFO order in the OrderedDict).
        assert "trigger-evict" in state._request_locks

    async def test_eviction_preserves_held_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even when the oldest entry is the one being held, the sweep
        # must skip it: dropping a Lock currently inside ``async with``
        # would let the next call mint a fresh Lock for the same id and
        # leave concurrent callers serialising on different objects.
        from synthorg.api import state_services_locks as _ss

        monkeypatch.setattr(_ss, "_MAX_REQUEST_LOCKS", 3)

        state = _make_state()
        oldest = state.get_or_create_request_lock("oldest")
        async with oldest:
            # Fill to cap with idle entries.
            state.get_or_create_request_lock("middle")
            state.get_or_create_request_lock("newest")
            # Trigger eviction: ``oldest`` is held, so it must survive
            # and one of the idle entries is dropped instead.
            state.get_or_create_request_lock("trigger")
            assert "oldest" in state._request_locks
            assert state._request_locks["oldest"] is oldest
            assert "trigger" in state._request_locks

    async def test_eviction_preserves_referenced_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The race the refcount fixes: between
        # ``acquire_request_lock`` reserving the Lock and the body
        # entering ``async with``, the Lock is unlocked but in flight.
        # An eviction sweep that ignores the refcount would drop it
        # under the caller's feet and the next call would mint a
        # different Lock, splitting the per-id serialisation guarantee.
        from synthorg.api import state_services_locks as _ss

        monkeypatch.setattr(_ss, "_MAX_REQUEST_LOCKS", 3)

        state = _make_state()
        # Simulate the in-flight window: refcount > 0, lock unlocked.
        reserved = state._reserve_request_lock("in-flight")
        try:
            # Fill registry to the cap with idle entries.
            state.get_or_create_request_lock("idle-1")
            state.get_or_create_request_lock("idle-2")
            # Trigger eviction sweep with one more insert.
            state.get_or_create_request_lock("trigger")
            # ``in-flight`` is unlocked but its refcount is non-zero,
            # so the sweep must keep it.
            assert "in-flight" in state._request_locks
            assert state._request_locks["in-flight"] is reserved
        finally:
            state._release_request_lock_ref("in-flight")


@pytest.mark.unit
class TestAppStateApiBridgeConfig:
    """Tests for api_bridge_config snapshot accessor and swap_api_bridge_config."""

    def test_default_snapshot_available_pre_startup(self) -> None:
        from synthorg.settings.bridge_configs import ApiBridgeConfig

        state = _make_state()
        snapshot = state.api_bridge_config
        assert isinstance(snapshot, ApiBridgeConfig)
        assert snapshot == ApiBridgeConfig()

    def test_default_lifecycle_cap_matches_bridge_default(self) -> None:
        from synthorg.settings.bridge_configs import ApiBridgeConfig

        state = _make_state()
        assert (
            state.api_bridge_config.max_lifecycle_events_per_query
            == ApiBridgeConfig().max_lifecycle_events_per_query
        )

    def test_swap_replaces_snapshot(self) -> None:
        from synthorg.settings.bridge_configs import ApiBridgeConfig

        state = _make_state()
        new = ApiBridgeConfig(max_lifecycle_events_per_query=25_000)
        state.swap_api_bridge_config(new)
        assert state.api_bridge_config is new
        assert state.api_bridge_config.max_lifecycle_events_per_query == 25_000

    def test_swap_is_idempotent_for_same_instance(self) -> None:
        from synthorg.settings.bridge_configs import ApiBridgeConfig

        state = _make_state()
        snapshot = ApiBridgeConfig(max_lifecycle_events_per_query=12_345)
        state.swap_api_bridge_config(snapshot)
        state.swap_api_bridge_config(snapshot)
        assert state.api_bridge_config is snapshot
