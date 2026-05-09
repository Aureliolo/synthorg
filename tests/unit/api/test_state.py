"""Tests for application state accessors."""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from tests.unit.api.fakes import (
    FakeMessageBus,
    FakePersistenceBackend,
)


def _make_state(**overrides: object) -> AppState:
    defaults: dict[str, object] = {
        "config": RootConfig(company_name="test"),
        "approval_store": ApprovalStore(),
    }
    defaults.update(overrides)
    return AppState(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestAppStateAccessors:
    def test_persistence_raises_when_none(self) -> None:
        state = _make_state(persistence=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.persistence

    def test_message_bus_raises_when_none(self) -> None:
        state = _make_state(message_bus=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.message_bus

    def test_cost_tracker_raises_when_none(self) -> None:
        state = _make_state(cost_tracker=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.cost_tracker

    async def test_persistence_returns_when_set(self) -> None:
        backend = FakePersistenceBackend()
        await backend.connect()
        state = _make_state(persistence=backend)
        assert state.persistence is backend

    async def test_message_bus_returns_when_set(self) -> None:
        bus = FakeMessageBus()
        await bus.start()
        state = _make_state(message_bus=bus)
        assert state.message_bus is bus

    def test_cost_tracker_returns_when_set(self) -> None:
        from synthorg.budget.tracker import CostTracker

        tracker = CostTracker()
        state = _make_state(cost_tracker=tracker)
        assert state.cost_tracker is tracker

    def test_auth_service_raises_when_none(self) -> None:
        state = _make_state(auth_service=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.auth_service

    def test_auth_service_returns_when_set(self) -> None:
        from synthorg.api.auth.service import AuthService
        from synthorg.core.auth.config import AuthConfig

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state(auth_service=svc)
        assert state.auth_service is svc

    def test_set_auth_service_succeeds_once(self) -> None:
        from synthorg.api.auth.service import AuthService
        from synthorg.core.auth.config import AuthConfig

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state()
        state.set_auth_service(svc)
        assert state.auth_service is svc

    def test_set_auth_service_twice_raises(self) -> None:
        from synthorg.api.auth.service import AuthService
        from synthorg.core.auth.config import AuthConfig

        secret = "test-secret-that-is-at-least-32-characters-long"
        svc = AuthService(AuthConfig(jwt_secret=secret))
        state = _make_state(auth_service=svc)
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_auth_service(svc)


@pytest.mark.unit
class TestAppStateTaskEngine:
    """Tests for task_engine property, has_task_engine, set_task_engine."""

    def test_task_engine_raises_when_none(self) -> None:
        state = _make_state(task_engine=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.task_engine

    def test_task_engine_returns_when_set(self) -> None:
        from unittest.mock import MagicMock

        engine = MagicMock()
        state = _make_state(task_engine=engine)
        assert state.task_engine is engine

    def test_has_task_engine_false_when_none(self) -> None:
        state = _make_state(task_engine=None)
        assert state.has_task_engine is False

    def test_has_task_engine_true_when_set(self) -> None:
        from unittest.mock import MagicMock

        engine = MagicMock()
        state = _make_state(task_engine=engine)
        assert state.has_task_engine is True

    def test_set_task_engine_succeeds_once(self) -> None:
        from unittest.mock import MagicMock

        engine = MagicMock()
        state = _make_state()
        state.set_task_engine(engine)
        assert state.task_engine is engine

    def test_set_task_engine_twice_raises(self) -> None:
        from unittest.mock import MagicMock

        engine = MagicMock()
        state = _make_state(task_engine=engine)
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_task_engine(engine)


@pytest.mark.unit
class TestAppStateCoordinator:
    """Tests for coordinator property and has_coordinator."""

    def test_coordinator_raises_when_none(self) -> None:
        state = _make_state(coordinator=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.coordinator

    def test_coordinator_returns_when_set(self) -> None:
        from unittest.mock import MagicMock

        coordinator = MagicMock()
        state = _make_state(coordinator=coordinator)
        assert state.coordinator is coordinator

    def test_has_coordinator_false_when_none(self) -> None:
        state = _make_state(coordinator=None)
        assert state.has_coordinator is False

    def test_has_coordinator_true_when_set(self) -> None:
        from unittest.mock import MagicMock

        coordinator = MagicMock()
        state = _make_state(coordinator=coordinator)
        assert state.has_coordinator is True


@pytest.mark.unit
class TestAppStateAgentRegistry:
    """Tests for agent_registry property."""

    def test_agent_registry_raises_when_none(self) -> None:
        state = _make_state(agent_registry=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.agent_registry

    def test_agent_registry_returns_when_set(self) -> None:
        from synthorg.hr.registry import AgentRegistryService

        registry = AgentRegistryService()
        state = _make_state(agent_registry=registry)
        assert state.agent_registry is registry

    def test_has_agent_registry_false_when_none(self) -> None:
        state = _make_state(agent_registry=None)
        assert state.has_agent_registry is False

    def test_has_agent_registry_true_when_set(self) -> None:
        from synthorg.hr.registry import AgentRegistryService

        registry = AgentRegistryService()
        state = _make_state(agent_registry=registry)
        assert state.has_agent_registry is True


@pytest.mark.unit
class TestAppStatePersistenceFlag:
    """Tests for has_persistence property."""

    def test_has_persistence_false_when_none(self) -> None:
        state = _make_state(persistence=None)
        assert state.has_persistence is False

    async def test_has_persistence_true_when_set(self) -> None:
        backend = FakePersistenceBackend()
        await backend.connect()
        state = _make_state(persistence=backend)
        assert state.has_persistence is True


@pytest.mark.unit
class TestAppStateMessageBusFlag:
    """Tests for has_message_bus property."""

    def test_has_message_bus_false_when_none(self) -> None:
        state = _make_state(message_bus=None)
        assert state.has_message_bus is False

    async def test_has_message_bus_true_when_set(self) -> None:
        bus = FakeMessageBus()
        await bus.start()
        state = _make_state(message_bus=bus)
        assert state.has_message_bus is True


@pytest.mark.unit
class TestAppStateSettingsServiceFlag:
    """Tests for has_settings_service property."""

    def test_has_settings_service_false_when_none(self) -> None:
        state = _make_state(settings_service=None)
        assert state.has_settings_service is False

    def test_has_settings_service_true_when_set(self) -> None:
        mock_svc = AsyncMock()
        state = _make_state(settings_service=mock_svc)
        assert state.has_settings_service is True


@pytest.mark.unit
class TestAppStateReviewGateService:
    """Tests for review_gate_service property and set_review_gate_service."""

    def test_review_gate_service_none_by_default(self) -> None:
        state = _make_state()
        assert state.review_gate_service is None

    def test_set_review_gate_service_succeeds_once(self) -> None:
        from unittest.mock import MagicMock

        svc = MagicMock()
        state = _make_state()
        state.set_review_gate_service(svc)
        assert state.review_gate_service is svc

    def test_set_review_gate_service_twice_raises(self) -> None:
        from unittest.mock import MagicMock

        svc = MagicMock()
        state = _make_state()
        state.set_review_gate_service(svc)
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_review_gate_service(svc)


@pytest.mark.unit
class TestAppStateApprovalTimeoutScheduler:
    """Tests for approval_timeout_scheduler and set_approval_timeout_scheduler."""

    def test_approval_timeout_scheduler_none_by_default(self) -> None:
        state = _make_state()
        assert state.approval_timeout_scheduler is None

    def test_set_approval_timeout_scheduler_succeeds_once(self) -> None:
        from unittest.mock import MagicMock

        scheduler = MagicMock()
        state = _make_state()
        state.set_approval_timeout_scheduler(scheduler)
        assert state.approval_timeout_scheduler is scheduler

    def test_set_approval_timeout_scheduler_twice_raises(self) -> None:
        from unittest.mock import MagicMock

        scheduler = MagicMock()
        state = _make_state()
        state.set_approval_timeout_scheduler(scheduler)
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_approval_timeout_scheduler(scheduler)


@pytest.mark.unit
class TestAppStateConfigResolver:
    """Tests for config_resolver property."""

    def test_config_resolver_raises_when_settings_service_none(self) -> None:
        state = _make_state(settings_service=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.config_resolver

    def test_config_resolver_returns_when_settings_service_set(self) -> None:
        from synthorg.settings.resolver import ConfigResolver

        mock_svc = AsyncMock()
        state = _make_state(settings_service=mock_svc)
        resolver = state.config_resolver
        assert isinstance(resolver, ConfigResolver)

    def test_config_resolver_is_singleton(self) -> None:
        """Successive property accesses return the same cached instance."""
        mock_svc = AsyncMock()
        state = _make_state(settings_service=mock_svc)
        first = state.config_resolver
        second = state.config_resolver
        assert first is second

    def test_has_config_resolver_false_when_none(self) -> None:
        state = _make_state(settings_service=None)
        assert state.has_config_resolver is False

    def test_has_config_resolver_true_when_set(self) -> None:
        mock_svc = AsyncMock()
        state = _make_state(settings_service=mock_svc)
        assert state.has_config_resolver is True


@pytest.mark.unit
class TestAppStateTrainingService:
    """Tests for training_service property and set_training_service."""

    def test_training_service_raises_when_none(self) -> None:
        state = _make_state(training_service=None)
        with pytest.raises(ServiceUnavailableError):
            _ = state.training_service

    def test_has_training_service_false_when_none(self) -> None:
        state = _make_state(training_service=None)
        assert state.has_training_service is False

    def test_set_training_service_once(self) -> None:
        state = _make_state()
        mock_svc = AsyncMock()
        state.set_training_service(mock_svc)
        assert state.training_service is mock_svc
        assert state.has_training_service is True

    def test_set_training_service_twice_raises(self) -> None:
        state = _make_state()
        mock_svc = AsyncMock()
        state.set_training_service(mock_svc)
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_training_service(mock_svc)


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
        from synthorg.api import state_services as _ss

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
        from synthorg.api import state_services as _ss

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
        from synthorg.api import state_services as _ss

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
