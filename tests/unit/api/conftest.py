"""Shared fixtures for API unit tests."""

import asyncio
import contextlib
import copy
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import argon2
import pytest
from litestar import Litestar
from litestar.testing import TestClient
from typeguard import suppress_type_checks

import synthorg.api.app as _app_mod
import synthorg.api.auth.service as _auth_mod
import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auth.service import AuthService
from synthorg.api.config import ApiConfig, RateLimitConfig
from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,
)
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.core.approval import ApprovalItem
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.audit import AuditLog
from synthorg.security.trust.config import TrustConfig
from synthorg.security.trust.service import TrustService
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from synthorg.tools.invocation_tracker import ToolInvocationTracker
from tests._shared import (
    LoopAsyncClient,
    as_uuid,
    mock_of,
)
from tests._shared import (
    build_test_app as create_app,
)
from tests._shared.trust import NoOpTrustStrategy
from tests.unit.api.fakes import (
    FakeArtifactStorage,
    FakeMessageBus,
    FakePersistenceBackend,
)

__all__ = ["FakeMessageBus", "FakePersistenceBackend"]

# Test-side ``@suppress_type_checks`` wrap on ``create_app``: the
# signature touches types behind source-side import cycles whose
# annotations typeguard's eager ``inspect.signature`` cannot resolve at
# runtime. Wrapping here (rather than at the source) keeps ``typeguard``
# a pure test dependency and confines the suppression to the call site
# that needs it.
create_app = suppress_type_checks(create_app)
_app_mod.create_app = create_app

# ── Test auth constants ───────────────────────────────────────

_TEST_JWT_SECRET = "test-secret-that-is-at-least-32-characters-long"
# Hardcoded valid Fernet key (deterministic across xdist workers).
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


# Production argon2 hasher uses memory_cost=65536 (64 MiB per hash)
# and parallelism=4. Test modules call ``make_auth_headers`` at MODULE
# IMPORT time (not inside a fixture), so the swap MUST happen at
# conftest import. With 8 xdist workers each collecting test modules
# concurrently, peak memory crosses 512 MiB and triggers
# ``argon2.exceptions.HashingError: Memory allocation error``. The
# lightweight hasher (8 KiB, parallelism=1) keeps the hash format and
# argon2 verification semantics intact while removing memory pressure.
_auth_mod._hasher = argon2.PasswordHasher(
    time_cost=1,
    memory_cost=8,  # 8 KiB instead of 64 MiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
)


# pytest-asyncio loops in the unit tier run under ``SelectorEventLoop``
# via the ``pytest_asyncio_loop_factories`` hook in
# ``tests/unit/conftest.py`` (Windows-only). The hook is the canonical
# seam for per-tier loop selection: it makes the choice explicit (so
# subprocess-driving tiers can shadow it with ``ProactorEventLoop``)
# and keeps the unit tier on a consistent loop type regardless of the
# Python default.


@pytest.fixture(scope="session", autouse=True)
def _required_env_vars() -> Iterator[None]:
    """Set bootstrap env vars + Cat-2 mirrors for API tests.

    Session-scoped with manual env-var management (``monkeypatch`` is
    function-scoped and cannot be used here). ``SYNTHORG_COMPANY_*``
    overrides set the company identity for tests; explicit env vars are
    the only mechanism (there is no YAML company-config tier).
    """
    import os

    _overrides = {
        "SYNTHORG_JWT_SECRET": _TEST_JWT_SECRET,
        "SYNTHORG_SETTINGS_KEY": _TEST_SETTINGS_KEY,
        "SYNTHORG_COMPANY_COMPANY_NAME": "test-company",
    }
    _previous = {name: os.environ.get(name) for name in _overrides}
    for name, value in _overrides.items():
        os.environ[name] = value
    yield
    for name, prior in _previous.items():
        if prior is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prior


@pytest.fixture(scope="session", autouse=True)
def _no_backup_service() -> Iterator[None]:
    """Disable the backup service in all API unit tests.

    Session-scoped with ``unittest.mock.patch`` (``monkeypatch`` is
    function-scoped and cannot be used here).
    """
    from unittest.mock import patch

    with patch(
        "synthorg.api.construction_phase.build_backup_service",
        return_value=None,
    ):
        yield


def make_exception_handler_app(handler: Any) -> Litestar:  # type: ignore[explicit-any]  # accepts any Litestar route handler
    """Build a minimal Litestar app with project exception handlers."""
    return Litestar(
        route_handlers=[handler],
        exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
    )


# ── Auth helpers ────────────────────────────────────────────────

# Cache password hashes by role so that make_auth_headers and
# _seed_test_users produce identical pwd_sig claims. The lock guards the
# check-then-compute-then-store sequence: ``make_auth_headers`` runs at
# module-import time across concurrently-collecting xdist workers, so two
# threads could otherwise both miss the cache and race the write.
_TEST_PASSWORD_HASHES: dict[str, str] = {}
_TEST_PASSWORD_HASHES_LOCK = threading.Lock()


def _make_test_auth_config() -> AuthConfig:
    """Create an AuthConfig with a test JWT secret."""
    return AuthConfig(jwt_secret=_TEST_JWT_SECRET)


def _make_test_auth_service() -> AuthService:
    """Create an AuthService backed by test config."""
    return AuthService(_make_test_auth_config())


def _get_test_password_hash(
    role: str,
    auth_service: AuthService,
) -> str:
    """Return a cached password hash for the given role.

    On the first call for a role, hashes the test password and
    caches the result so that ``make_auth_headers`` and
    ``_seed_test_users`` produce tokens with matching ``pwd_sig``
    claims.

    Callable from both sync and async contexts: when invoked from
    inside a running event loop (e.g. an ``async`` fixture body),
    ``asyncio.run`` would raise ``RuntimeError: cannot be called
    from a running event loop``.  Detect that case and run the
    coroutine in a worker thread with its own loop instead.
    """
    with _TEST_PASSWORD_HASHES_LOCK:
        if role in _TEST_PASSWORD_HASHES:
            return _TEST_PASSWORD_HASHES[role]
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop -- safe to use asyncio.run directly.
            _TEST_PASSWORD_HASHES[role] = asyncio.run(
                auth_service.hash_password("test-password-12chars"),
            )
        else:
            # Already inside a loop; run the hashing in a worker thread
            # so we do not nest event loops on the same thread.
            result: list[str] = []
            errors: list[BaseException] = []

            def _hash_in_thread() -> None:
                try:
                    result.append(
                        asyncio.run(
                            auth_service.hash_password(
                                "test-password-12chars",
                            ),
                        ),
                    )
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=_hash_in_thread)
            thread.start()
            thread.join()
            if errors:
                raise errors[0]
            _TEST_PASSWORD_HASHES[role] = result[0]
        return _TEST_PASSWORD_HASHES[role]


def make_auth_headers(
    role: str = "ceo",
    *,
    must_change_password: bool = False,
) -> dict[str, str]:
    """Build an Authorization header with a JWT for the given role.

    Uses deterministic user IDs matching ``_seed_test_users`` so
    middleware user lookups succeed.  The password hash is cached
    per role to ensure the ``pwd_sig`` claim matches the seeded
    user in persistence.
    """
    auth_service = _make_test_auth_service()
    # Must match the ID pattern in _seed_test_users
    user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"test-{role}"))
    now = datetime.now(UTC)
    user = User(
        id=user_id,
        username=f"test-{role}",
        password_hash=_get_test_password_hash(role, auth_service),
        role=HumanRole(role),
        must_change_password=must_change_password,
        created_at=now,
        updated_at=now,
    )
    token, _, _ = auth_service.create_token(user)
    return {"Authorization": f"Bearer {token}"}


# ── Fixtures ────────────────────────────────────────────────────
#
# The underlying service/app fixtures in this file are session-scoped:
# created once per xdist worker and shared across tests.  The
# ``async_test_client`` / ``ws_test_client`` fixtures are
# function-scoped, though, and perform per-test clearing/reconnection.
# The shared app uses ``_skip_lifecycle_shutdown=True`` to prevent
# lifespan shutdown from stopping/disconnecting shared services.  Tests
# that create their own apps may disconnect/stop the shared
# persistence/bus, but the per-test reset re-connects them before each
# test.


@pytest.fixture(scope="session")
def auth_config() -> AuthConfig:
    return _make_test_auth_config()


@pytest.fixture(scope="session")
def auth_service() -> AuthService:
    return _make_test_auth_service()


@pytest.fixture(scope="session")
def fake_persistence() -> FakePersistenceBackend:
    backend = FakePersistenceBackend()
    backend._connected = True
    return backend


@pytest.fixture(scope="session")
def fake_message_bus() -> FakeMessageBus:
    bus = FakeMessageBus()
    bus._running = True
    return bus


@pytest.fixture(scope="session")
def cost_tracker() -> CostTracker:
    return CostTracker()


@pytest.fixture(scope="session")
def approval_store() -> ApprovalStore:
    return ApprovalStore()


@pytest.fixture(scope="session")
def event_stream_hub() -> EventStreamHub:
    return EventStreamHub()


@pytest.fixture(scope="session")
def interrupt_store() -> InterruptStore:
    return InterruptStore()


@pytest.fixture(scope="session")
def root_config() -> RootConfig:
    from synthorg.integrations.config import IntegrationsConfig

    return RootConfig(
        company_name="test-company",
        api=ApiConfig(
            rate_limit=RateLimitConfig(
                # Floor must be >= auth_max_requests (validator);
                # raise it in lockstep so test traffic never trips
                # the IP floor under xdist parallelism.
                floor_max_requests=1_000_000,
                unauth_max_requests=1_000_000,
                auth_max_requests=1_000_000,
            ),
        ),
        integrations=IntegrationsConfig(enabled=False),
    )


@pytest.fixture(scope="session")
def performance_tracker() -> PerformanceTracker:
    return PerformanceTracker()


@pytest.fixture(scope="session")
def agent_registry(fake_persistence: FakePersistenceBackend) -> AgentRegistryService:
    from synthorg.versioning import VersioningService

    return AgentRegistryService(
        versioning=VersioningService(fake_persistence.identity_versions),
    )


@pytest.fixture(scope="session")
def provider_registry() -> ProviderRegistry:
    """A deterministic scripted provider so the shared app is not empty-company.

    The shared controller tests predate the empty-company guard
    (`AgentRuntimeNotConfiguredError`); they expect task creation /
    coordination to succeed. Registering one scripted provider keeps
    `has_active_provider` True without any LLM spend. Tests that need
    the no-provider path build their own app (see
    tests/integration/api/test_task_create_empty_company.py).
    """
    return ProviderRegistry.from_config(
        {"test-provider": ProviderConfig(driver="scripted")},
    )


@pytest.fixture(scope="session")
def provider_health_tracker() -> ProviderHealthTracker:
    return ProviderHealthTracker()


@pytest.fixture(scope="session")
def tool_invocation_tracker() -> ToolInvocationTracker:
    return ToolInvocationTracker()


@pytest.fixture(scope="session")
def delegation_record_store() -> DelegationRecordStore:
    return DelegationRecordStore()


@pytest.fixture(scope="session")
def fake_task_engine(
    fake_persistence: FakePersistenceBackend,
) -> TaskEngine:
    return TaskEngine(persistence=fake_persistence)


@pytest.fixture(scope="session")
def audit_log() -> AuditLog:
    return AuditLog()


@pytest.fixture(scope="session")
def trust_service() -> TrustService:
    return TrustService(
        strategy=NoOpTrustStrategy(),
        config=TrustConfig(),
    )


@pytest.fixture(scope="session")
def coordination_metrics_store() -> CoordinationMetricsStore:
    return CoordinationMetricsStore()


@pytest.fixture(scope="session")
def task_board_entry_adapter() -> TaskBoardEntryAdapter:
    """A board entry adapter backed by a stub :class:`WorkPipeline`.

    The shared API tests predate the entry-adapter switch on
    ``POST /tasks``; they expect creation to succeed. Wiring a real
    adapter with a mock pipeline keeps ``has_task_board_entry_adapter``
    True without doing any spine work in the unit suite. The
    integration test ``test_task_create_empty_company.py`` exercises
    the absent-adapter / 409 path explicitly.
    """
    pipeline = mock_of[WorkPipeline]()

    async def _no_run(_work_item: object) -> None:
        # The detached background task swallows the return value; we
        # only need ``submit`` not to raise. A real spine result is
        # exercised in the e2e suite.
        return None

    pipeline.run.side_effect = _no_run
    return TaskBoardEntryAdapter(work_pipeline=pipeline)


# ── Session-scoped shared app ─────────────────────────────────


@pytest.fixture(scope="session")
def _shared_app(  # noqa: PLR0913
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    fake_task_engine: TaskEngine,
    cost_tracker: CostTracker,
    approval_store: ApprovalStore,
    root_config: RootConfig,
    auth_service: AuthService,
    performance_tracker: PerformanceTracker,
    agent_registry: AgentRegistryService,
    provider_registry: ProviderRegistry,
    provider_health_tracker: ProviderHealthTracker,
    tool_invocation_tracker: ToolInvocationTracker,
    delegation_record_store: DelegationRecordStore,
    audit_log: AuditLog,
    trust_service: TrustService,
    coordination_metrics_store: CoordinationMetricsStore,
    event_stream_hub: EventStreamHub,
    interrupt_store: InterruptStore,
    task_board_entry_adapter: TaskBoardEntryAdapter,
) -> Litestar:
    """Build the Litestar app ONCE per xdist worker.

    Uses ``_skip_lifecycle_shutdown=True`` so startup hooks run
    normally but shutdown is empty.  The startup hooks are
    idempotent (guarded by ``has_*`` checks and ``is_running``
    flags), so re-running them per-test is safe and near-instant.
    """
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )

    # ``create_app`` is wrapped with ``@suppress_type_checks`` at conftest
    # import time -- see the module-top wrapping. Wrapping here (rather
    # than at the source) keeps ``typeguard`` a pure test dependency.
    return create_app(
        config=root_config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=cost_tracker,
        approval_store=approval_store,
        auth_service=auth_service,
        task_engine=fake_task_engine,
        performance_tracker=performance_tracker,
        agent_registry=agent_registry,
        settings_service=settings_service,
        provider_registry=provider_registry,
        provider_health_tracker=provider_health_tracker,
        tool_invocation_tracker=tool_invocation_tracker,
        delegation_record_store=delegation_record_store,
        artifact_storage=FakeArtifactStorage(),
        audit_log=audit_log,
        trust_service=trust_service,
        coordination_metrics_store=coordination_metrics_store,
        event_stream_hub=event_stream_hub,
        interrupt_store=interrupt_store,
        task_board_entry_adapter=task_board_entry_adapter,
        _skip_lifecycle_shutdown=True,
    )


# ── Function-scoped async_test_client / ws_test_client with reset ──


def _restore_instance_patches(obj: object) -> None:
    """Remove instance-level method patches from session-scoped services.

    Session-scoped fixtures are shared across tests within an xdist
    worker.  Without this cleanup, monkeypatches applied by one test
    leak into subsequent tests and silently corrupt their behaviour.
    ``clear()`` resets service data but does not undo method patches.

    Tests like ``test_degraded_tool_tracker`` patch methods directly
    on session-scoped instances (``tracker.get_records = _raise``).
    Tests also patch private methods (``tracker._evict = _raise``) via
    ``monkeypatch`` / ``unittest.mock.patch.object`` to simulate
    internal failures.  This function removes any instance attribute
    that shadows a class-level callable -- including single-underscore
    "private" methods -- restoring the original method from the class
    so the next test sees the unpatched implementation.  Dunders are
    skipped to avoid touching Python's protocol attributes.

    Skips ``__slots__``-only classes: they have no ``__dict__``, and
    ``__slots__`` prevents arbitrary attribute assignment, so they
    cannot carry instance-level patches to begin with.
    """
    obj_dict = getattr(obj, "__dict__", None)
    if obj_dict is None:
        return
    cls = type(obj)
    for attr in list(obj_dict):
        if attr.startswith("__"):
            continue
        if callable(getattr(cls, attr, None)):
            delattr(obj, attr)


@dataclass(frozen=True)
class _ResetServices:
    """Session-scoped services the per-test reset clears and re-seeds."""

    fake_persistence: FakePersistenceBackend
    fake_message_bus: FakeMessageBus
    cost_tracker: CostTracker
    approval_store: ApprovalStore
    performance_tracker: PerformanceTracker
    agent_registry: AgentRegistryService
    provider_health_tracker: ProviderHealthTracker
    tool_invocation_tracker: ToolInvocationTracker
    delegation_record_store: DelegationRecordStore
    audit_log: AuditLog
    coordination_metrics_store: CoordinationMetricsStore
    auth_service: AuthService


def _reset_service_state(services: _ResetServices) -> None:
    """Step 1: clear mutable service state and undo any method patches.

    Restores original methods BEFORE ``clear()``: a prior test may have
    monkeypatched ``svc.clear`` (or a method it calls) with a stub that
    raises or corrupts state, so the real implementation must run.
    ``AgentRegistryService`` / ``ApprovalStore`` ``clear`` are async, so
    their sync test-only entry points are used to keep the reset sync.
    """
    tracked = (
        services.cost_tracker,
        services.approval_store,
        services.performance_tracker,
        services.agent_registry,
        services.provider_health_tracker,
        services.tool_invocation_tracker,
        services.delegation_record_store,
        services.audit_log,
        services.coordination_metrics_store,
    )
    for svc in tracked:
        _restore_instance_patches(svc)
        if isinstance(svc, AgentRegistryService):
            from synthorg.hr.registry_testing import reset_registry_for_test_sync

            reset_registry_for_test_sync(svc)
        elif isinstance(svc, ApprovalStore):
            svc.reset_for_test_sync()
        else:
            svc.clear()
    services.fake_persistence.clear()
    services.fake_message_bus.clear()


def _reset_task_engine(app_state: AppState) -> None:
    """Step 2: rebind the session-scoped task engine to the next test's loop.

    The engine's queues accumulate pending put/get futures bound to the
    *previous* test's event loop; once that loop closes those futures are
    permanently unusable and block any further async queue interaction.
    Recreating the queues and resetting ``_running`` lets the next startup
    create fresh processing tasks on the new loop. Still required under the
    portal-free async client: pytest-asyncio's per-test loop closes after
    every test (loop scope is "function"), so the same dead-loop applies.

    ``asyncio.Queue`` does NOT bind to a loop at construction time (its
    ``_loop`` is resolved lazily on the first ``put``/``get``). So even
    though this runs inside the test's running loop, the fresh queues stay
    unbound until the engine's processing tasks first drive them on the
    next startup, binding them to that test's loop.
    """
    from synthorg.engine.state import EngineStateSlice

    task_engine = app_state.slice(EngineStateSlice).task_engine
    if task_engine is None:
        return
    task_engine._running = False
    task_engine._queue = asyncio.Queue(maxsize=task_engine._config.max_queue_size)
    task_engine._observer_queue = asyncio.Queue(
        maxsize=task_engine._config.effective_observer_queue_size,
    )
    task_engine._versions = type(task_engine._versions)()
    task_engine._observers.clear()


def _clear_appstate_stores(shared_app: Litestar, app_state: AppState) -> None:
    """Step 3: clear AppState-internal caches + the per-op rate-limit store.

    Session and lockout stores are kept (rebuilding from DB every test is
    expensive); their in-memory caches are cleared in place so
    revoked-session / lockout state cannot bleed across tests.
    """
    from synthorg.api.api_core_state import ApiCoreStateSlice
    from synthorg.communication.state import CommunicationStateSlice
    from synthorg.settings.state import SettingsStateSlice

    api_core = app_state.slice(ApiCoreStateSlice)
    if api_core.session_store is not None:
        api_core.session_store._revoked.clear()
    if api_core.lockout_store is not None:
        # _locked is an internal cache on the concrete store; the
        # LockoutStore Protocol exposes only the public API.
        api_core.lockout_store._locked.clear()  # type: ignore[attr-defined]
    if api_core.ticket_store is not None:
        api_core.ticket_store._tickets.clear()
    if api_core.user_presence is not None:
        api_core.user_presence._counts.clear()
    communication = app_state.slice(CommunicationStateSlice)
    if communication.interrupt_store is not None:
        communication.interrupt_store._pending.clear()
        communication.interrupt_store._events.clear()
        communication.interrupt_store._results.clear()
    if communication.event_stream_hub is not None:
        communication.event_stream_hub._subscribers.clear()
    settings_service = app_state.slice(SettingsStateSlice).settings_service
    if settings_service is not None:
        settings_service._cache.clear()
    # Clear the escalation queue + pending-future registry so a prior
    # test's in-flight escalations cannot bleed into the next one.
    if communication.escalation_store is not None:
        communication.escalation_store._rows.clear()  # type: ignore[attr-defined]
    if communication.escalation_registry is not None:
        communication.escalation_registry._futures.clear()

    # Clear the per-op rate-limit sliding-window store so a prior test's
    # 429 buckets (e.g. ``setup.complete`` at 5/3600s) cannot bleed over.
    per_op_store = getattr(shared_app.state, "per_op_rate_limit_store", None)
    if per_op_store is not None:
        buckets = getattr(per_op_store, "_buckets", None)
        if isinstance(buckets, dict):
            buckets.clear()
        locks = getattr(per_op_store, "_locks", None)
        if isinstance(locks, dict):
            locks.clear()


# Cohesive owner objects composed onto ``AppState`` to hold the mutable
# runtime state a frozen slice cannot own. Their private fields are
# snapshotted and restored per test (alongside the identity primitives on
# ``AppState`` itself) so a test's in-place mutation cannot bleed into the
# next test sharing the session-scoped app.
_PRIMITIVE_OWNER_ATTRS: tuple[str, ...] = (
    "bridge_config",
    "per_op_limits",
    "request_locks",
    "ws_auth_limits",
)
# Slice-store internals are reverted wholesale via ``saved_slices``, not
# as individual primitive fields.
_NON_PRIMITIVE_PRIVATE_ATTRS: frozenset[str] = frozenset({"_slices", "_slice_lock"})


def _mro_slot_names(obj: object) -> list[str]:
    """Every ``__slots__`` name declared across ``type(obj)``'s MRO."""
    names: list[str] = []
    for klass in type(obj).__mro__:
        names.extend(getattr(klass, "__slots__", ()))
    return names


def _iter_primitive_holders(app_state: AppState) -> Iterator[tuple[object, str]]:
    """Yield ``(holder, attr)`` for every mutable primitive to snapshot.

    Walks ``app_state``'s own private state (``__slots__`` and/or
    ``__dict__``) plus the private fields of each composed primitive
    owner object (``bridge_config`` / ``per_op_limits`` / ``request_locks``
    / ``ws_auth_limits``). The slice store is excluded (restored wholesale
    elsewhere). Owner objects are never reconstructed, so restoring onto
    the captured instance reverts a test's in-place config swaps.
    """
    seen: set[str] = set()
    for name in _mro_slot_names(app_state):
        if name.startswith("_") and name not in _NON_PRIMITIVE_PRIVATE_ATTRS:
            seen.add(name)
            yield app_state, name
    instance_dict: dict[str, object] = getattr(app_state, "__dict__", {})
    for name in list(instance_dict):
        if (
            name.startswith("_")
            and name not in _NON_PRIMITIVE_PRIVATE_ATTRS
            and name not in seen
        ):
            yield app_state, name
    for owner_attr in _PRIMITIVE_OWNER_ATTRS:
        owner = getattr(app_state, owner_attr, None)
        if owner is not None:
            for name in _mro_slot_names(owner):
                yield owner, name


def _snapshot_app_state(
    app_state: AppState,
) -> tuple[
    list[tuple[object, str, object]],
    dict[type[BaseFeatureStateSlice], BaseFeatureStateSlice],
]:
    """Step 5: snapshot mutable primitives + the per-feature slice store.

    Each primitive owner exposes its mutable runtime state via slots;
    every domain service lives on a frozen feature slice in ``_slices``,
    so a shallow copy of that mapping is enough to revert a test's
    ``wire`` / ``swap_slice`` mutations (the slice *values* are immutable,
    so they need no deep copy). Mutable *container* primitives (the
    request-lock registry's dict/refcount map, the background-task sets)
    are snapshotted by value (shallow copy) so a test's in-place mutation
    is reverted on restore; scalars, frozen configs, and lock objects are
    captured by reference.
    """
    saved: list[tuple[object, str, object]] = []
    for holder, name in _iter_primitive_holders(app_state):
        value = getattr(holder, name)
        if isinstance(value, (dict, set, list)):
            value = copy.copy(value)
        saved.append((holder, name, value))
    saved_slices: dict[type[BaseFeatureStateSlice], BaseFeatureStateSlice] = dict(
        app_state._slices
    )
    return saved, saved_slices


def _clear_litestar_stores(shared_app: Litestar) -> None:
    """Step 6: clear Litestar-internal rate-limit stores.

    Reaches into private attributes (``stores._stores`` / ``store._store``)
    because Litestar has no public bulk-clear API. Guarded with ``hasattr``
    so a Litestar upgrade fails with a clear, actionable error instead of a
    cryptic ``AttributeError`` deep in fixture setup.
    """
    shared_stores = shared_app.stores
    if not hasattr(shared_stores, "_stores"):
        msg = (
            "Test fixture expected Litestar app.stores to expose a "
            "private '_stores' mapping for rate-limit reset, but it "
            "was not found. Litestar internals may have changed; "
            "update this fixture to use a supported store-clearing "
            "API if available."
        )
        raise RuntimeError(msg)
    for store in shared_stores._stores.values():
        inner = getattr(store, "_store", None)
        if inner is None or not hasattr(inner, "clear"):
            msg = (
                "Test fixture expected each Litestar store to expose "
                "a private '_store' object with a 'clear()' method "
                "for rate-limit reset, but the internal structure "
                "did not match. Litestar internals may have changed; "
                "update this fixture to use a supported "
                "store-clearing API if available."
            )
            raise RuntimeError(msg)
        inner.clear()


def _pre_test_reset(
    shared_app: Litestar,
    services: _ResetServices,
) -> tuple[
    list[tuple[object, str, object]],
    dict[type[BaseFeatureStateSlice], BaseFeatureStateSlice],
]:
    """Clear shared mutable state and snapshot AppState (steps 1-6).

    Returns the AppState primitive snapshot and a copy of the slice
    store so the caller can restore them after the test (see
    :func:`_restore_app_state`). Shared by the sync and async client
    context managers: the reset is identical regardless of client type (it
    exists because the app + services are session-scoped, not because of
    the client).
    """
    app_state: AppState = shared_app.state.app_state
    _reset_service_state(services)
    # Re-connect persistence: a prior test's lifespan shutdown may have
    # disconnected it (``_skip_lifecycle_shutdown`` keeps the engine's
    # ``_running`` True, but its loop-bound tasks die with the old loop).
    services.fake_persistence._connected = True
    _reset_task_engine(app_state)
    _clear_appstate_stores(shared_app, app_state)
    _seed_test_users(services.fake_persistence, services.auth_service)
    saved, saved_slices = _snapshot_app_state(app_state)
    _clear_litestar_stores(shared_app)
    return saved, saved_slices


def _post_startup_reset(shared_app: Litestar, services: _ResetServices) -> None:
    """Re-baseline once-only startup wiring and re-seed (step 7, post-enter).

    Run AFTER the client enters the app lifespan. ``create_app``'s
    ``_install_runtime_services`` / ``_wire_docs_engine`` / workspace boot
    hooks are once-only (closure flags), but the session-scoped shared app
    re-runs lifespan startup every test, so only the FIRST test per worker
    observes them wired. Force the no-runtime-services / empty-docs /
    no-workspace baseline so every test sees identical state (tests that
    need those inject their own via the client). Startup also creates a
    system user and mutates settings, so re-clear persistence + the
    settings cache and re-seed users here.
    """
    from synthorg.docs_engine.state import DocsStateSlice
    from synthorg.engine.workspace.state import WorkspaceStateSlice
    from synthorg.settings.state import SettingsStateSlice
    from synthorg.workers.state import RuntimeStateSlice

    fake_persistence = services.fake_persistence
    auth_service = services.auth_service
    app_state: AppState = shared_app.state.app_state

    fake_persistence.clear()
    fake_persistence._connected = True
    post_startup_settings = app_state.slice(SettingsStateSlice).settings_service
    if post_startup_settings is not None:
        post_startup_settings._cache.clear()
    app_state.wire(
        RuntimeStateSlice,
        coordinator=None,
        worker_execution_service=None,
    )
    app_state.swap_slice(DocsStateSlice())
    app_state.wire(WorkspaceStateSlice, project_workspace_service=None)
    _seed_test_users(fake_persistence, auth_service)
    _promote_first_owner(fake_persistence)


def _restore_app_state(
    shared_app: Litestar,
    saved: list[tuple[object, str, object]],
    saved_slices: dict[type[BaseFeatureStateSlice], BaseFeatureStateSlice],
) -> None:
    """Restore AppState primitives + slice store after the test (step 8).

    Reverts any ``wire`` / ``swap_slice`` a test performed (and the
    post-startup baseline) so a coordinator / docs runtime a test injected
    cannot bleed into the next test sharing the session-scoped app. The
    primitive fields are written back onto their captured holders (the
    facade and its owner objects), reverting in-place config swaps.
    """
    app_state: AppState = shared_app.state.app_state
    for holder, name, value in saved:
        setattr(holder, name, value)
    app_state._slices.clear()
    app_state._slices.update(saved_slices)


@contextlib.contextmanager
def _sync_shared_client(
    shared_app: Litestar,
    services: _ResetServices,
) -> Iterator[TestClient[Litestar]]:
    """Reset state, enter a sync ``TestClient`` on the shared app, restore."""
    saved, saved_slices = _pre_test_reset(shared_app, services)
    try:
        with TestClient(shared_app) as client:
            _post_startup_reset(shared_app, services)
            client.headers.update(make_auth_headers("ceo"))
            yield client
    finally:
        # Restore even if client entry / post-startup reset raises, so a
        # failed test cannot leak a wired slice store into the next test.
        _restore_app_state(shared_app, saved, saved_slices)


@contextlib.asynccontextmanager
async def _async_shared_client(
    shared_app: Litestar,
    services: _ResetServices,
) -> AsyncIterator[LoopAsyncClient]:
    """Reset state, enter a portal-free async client, restore.

    The lifespan startup and every request run on the caller's
    pytest-asyncio loop (no BlockingPortal), so a test creates exactly
    one event loop and zero portals.
    """
    saved, saved_slices = _pre_test_reset(shared_app, services)
    try:
        async with LoopAsyncClient(shared_app) as client:
            _post_startup_reset(shared_app, services)
            client.headers.update(make_auth_headers("ceo"))
            yield client
    finally:
        # Restore even if client entry / post-startup reset raises, so a
        # failed test cannot leak a wired slice store into the next test.
        _restore_app_state(shared_app, saved, saved_slices)


@pytest.fixture
def _reset_services(  # noqa: PLR0913
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    cost_tracker: CostTracker,
    approval_store: ApprovalStore,
    performance_tracker: PerformanceTracker,
    agent_registry: AgentRegistryService,
    provider_health_tracker: ProviderHealthTracker,
    tool_invocation_tracker: ToolInvocationTracker,
    delegation_record_store: DelegationRecordStore,
    audit_log: AuditLog,
    coordination_metrics_store: CoordinationMetricsStore,
    auth_service: AuthService,
) -> _ResetServices:
    """Bundle the session-scoped services the per-test reset operates on."""
    return _ResetServices(
        fake_persistence=fake_persistence,
        fake_message_bus=fake_message_bus,
        cost_tracker=cost_tracker,
        approval_store=approval_store,
        performance_tracker=performance_tracker,
        agent_registry=agent_registry,
        provider_health_tracker=provider_health_tracker,
        tool_invocation_tracker=tool_invocation_tracker,
        delegation_record_store=delegation_record_store,
        audit_log=audit_log,
        coordination_metrics_store=coordination_metrics_store,
        auth_service=auth_service,
    )


@pytest.fixture
async def async_test_client(
    _shared_app: Litestar,
    _reset_services: _ResetServices,
) -> AsyncIterator[LoopAsyncClient]:
    """Yield a portal-free async client wrapping the shared app.

    The expensive ``create_app()`` runs once per worker. Each test
    re-runs the idempotent lifespan startup (~90ms) and the per-test
    state reset; shutdown is skipped (``_skip_lifecycle_shutdown``). The
    lifespan and every request run on the test's own pytest-asyncio loop,
    so there is no anyio ``BlockingPortal`` (no extra thread, event loop,
    or ``socket.socketpair``).
    """
    async with _async_shared_client(_shared_app, _reset_services) as client:
        yield client


@pytest.fixture
def ws_test_client(
    _shared_app: Litestar,
    _reset_services: _ResetServices,
) -> Iterator[TestClient[Litestar]]:
    """Yield a sync ``TestClient`` for websocket tests.

    litestar 2.22's ``WebSocketTestSession`` is sync and portal-backed in
    its ``__enter__``/``__exit__`` contract regardless of whether it is
    obtained from a sync or async client (``AsyncTestClient.websocket_connect``
    returns the same session), so websocket tests keep a sync client. Only
    a handful of tests use this, so the per-test portal it creates is a
    negligible ``socket.socketpair`` load.
    """
    with _sync_shared_client(_shared_app, _reset_services) as client:
        yield client


def _promote_first_owner(backend: FakePersistenceBackend) -> None:
    """Promote the first seeded user to OWNER.

    Replicates ``_maybe_promote_first_owner`` from the lifespan
    startup.  Called after seeding test users to ensure at least
    one user has ``OrgRole.OWNER``, matching the production
    startup behavior.
    """
    from synthorg.core.auth.models import OrgRole

    users = backend._users._users
    if not users:
        return
    first_id = next(iter(users))
    first = users[first_id]
    if OrgRole.OWNER not in first.org_roles:
        users[first_id] = first.model_copy(
            update={"org_roles": (*first.org_roles, OrgRole.OWNER)},
        )


def _seed_test_users(
    backend: FakePersistenceBackend,
    auth_service: AuthService,
) -> None:
    """Pre-seed a user for each role so JWT validation succeeds.

    The middleware looks up the user by ``sub`` claim, so we
    need matching users in the fake persistence for every role
    that tests might use.  Uses cached password hashes to ensure
    ``pwd_sig`` claims match between seeded users and tokens
    produced by ``make_auth_headers``.

    Assigns directly to the fake repository's internal dict
    (avoiding async) so this helper works in both sync fixtures
    and sync test functions.
    """
    now = datetime.now(UTC)
    for role in HumanRole:
        user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"test-{role.value}"))
        user = User(
            id=user_id,
            username=f"test-{role.value}",
            password_hash=_get_test_password_hash(
                role.value,
                auth_service,
            ),
            role=role,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        backend._users._users[user.id] = user


def make_task(  # noqa: PLR0913
    *,
    task_id: str = "task-001",
    title: str = "Test task",
    description: str = "A test task",
    project: str = "test-project",
    created_by: str = "alice",
    status: TaskStatus = TaskStatus.CREATED,
    assigned_to: str | None = None,
) -> Task:
    """Build a Task with sensible defaults."""
    from synthorg.core.task_enums import TaskType

    if assigned_to is None and status in {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.COMPLETED,
    }:
        assigned_to = "alice"
    return Task(
        id=as_uuid(task_id),
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        project=project,
        created_by=created_by,
        status=status,
        assigned_to=assigned_to,
    )


def make_approval(  # noqa: PLR0913
    *,
    approval_id: str = "approval-001",
    action_type: str = "code_merge",
    title: str = "Test approval",
    description: str = "A test approval item",
    requested_by: str = "agent-dev",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM,
    status: ApprovalStatus = ApprovalStatus.PENDING,
    ttl_seconds: int | None = None,
    task_id: str | None = None,
) -> ApprovalItem:
    """Build an ApprovalItem with sensible defaults."""
    now = datetime.now(UTC)
    expires_at = None
    if ttl_seconds is not None:
        expires_at = now + timedelta(seconds=ttl_seconds)
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=action_type,
        title=title,
        description=description,
        requested_by=requested_by,
        risk_level=risk_level,
        status=status,
        created_at=now,
        expires_at=expires_at,
        task_id=task_id,
    )
