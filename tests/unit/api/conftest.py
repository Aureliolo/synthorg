"""Shared fixtures for API unit tests."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import argon2
import pytest
from litestar import Litestar
from litestar.testing import TestClient

import synthorg.api.auth.service as _auth_mod
import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.api.app import create_app
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auth.config import AuthConfig
from synthorg.api.auth.models import User
from synthorg.api.auth.service import AuthService
from synthorg.api.config import ApiConfig, RateLimitConfig
from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.api.guards import HumanRole
from synthorg.api.state import AppState
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,
)
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.config.schema import RootConfig
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
    TaskStatus,
)
from synthorg.core.task import Task
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.health import ProviderHealthTracker
from synthorg.security.audit import AuditLog
from synthorg.security.trust.config import TrustConfig
from synthorg.security.trust.disabled_strategy import DisabledTrustStrategy
from synthorg.security.trust.service import TrustService
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from synthorg.tools.invocation_tracker import ToolInvocationTracker
from tests.unit.api.fakes import (
    FakeArtifactStorage,
    FakeMessageBus,
    FakePersistenceBackend,
)

__all__ = ["FakeMessageBus", "FakePersistenceBackend"]

# ── Test auth constants ───────────────────────────────────────

_TEST_JWT_SECRET = "test-secret-that-is-at-least-32-characters-long"
# Hardcoded valid Fernet key (deterministic across xdist workers).
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="
_TEST_USER_ID = "test-user-001"
_TEST_USERNAME = "testadmin"


@pytest.fixture(scope="session", autouse=True)
def _lightweight_argon2_hasher() -> Any:
    """Replace the production argon2 hasher with a lightweight one.

    The production hasher uses ``memory_cost=65536`` (64 MiB per hash)
    and ``parallelism=4``.  With 8 xdist workers each creating multiple
    ``TestClient`` fixtures that hash passwords during user seeding,
    peak memory reaches several hundred MB and triggers
    ``argon2.exceptions.HashingError: Memory allocation error``.

    Session-scoped so every worker replaces the module global exactly
    once and restores it on teardown, avoiding isolation drift.
    """
    original = _auth_mod._hasher
    _auth_mod._hasher = argon2.PasswordHasher(
        time_cost=1,
        memory_cost=8,  # 8 KiB instead of 64 MiB
        parallelism=1,
        hash_len=32,
        salt_len=16,
    )
    yield
    _auth_mod._hasher = original


@pytest.fixture(scope="session", autouse=True)
def _required_env_vars() -> Iterator[None]:
    """Set SYNTHORG_JWT_SECRET and SYNTHORG_SETTINGS_KEY for API tests.

    Session-scoped with manual env-var management (``monkeypatch`` is
    function-scoped and cannot be used here).
    """
    import os

    old_jwt = os.environ.get("SYNTHORG_JWT_SECRET")
    old_key = os.environ.get("SYNTHORG_SETTINGS_KEY")
    os.environ["SYNTHORG_JWT_SECRET"] = _TEST_JWT_SECRET
    os.environ["SYNTHORG_SETTINGS_KEY"] = _TEST_SETTINGS_KEY
    yield
    if old_jwt is None:
        os.environ.pop("SYNTHORG_JWT_SECRET", None)
    else:
        os.environ["SYNTHORG_JWT_SECRET"] = old_jwt
    if old_key is None:
        os.environ.pop("SYNTHORG_SETTINGS_KEY", None)
    else:
        os.environ["SYNTHORG_SETTINGS_KEY"] = old_key


@pytest.fixture(scope="session", autouse=True)
def _no_backup_service() -> Iterator[None]:
    """Disable the backup service in all API unit tests.

    Session-scoped with ``unittest.mock.patch`` (``monkeypatch`` is
    function-scoped and cannot be used here).
    """
    from unittest.mock import patch

    with patch(
        "synthorg.api.app.build_backup_service",
        return_value=None,
    ):
        yield


def make_exception_handler_app(handler: Any) -> Litestar:
    """Build a minimal Litestar app with project exception handlers."""
    return Litestar(
        route_handlers=[handler],
        exception_handlers=dict(EXCEPTION_HANDLERS),  # type: ignore[arg-type]
    )


# ── Auth helpers ────────────────────────────────────────────────

# Cache password hashes by role so that make_auth_headers and
# _seed_test_users produce identical pwd_sig claims.
_TEST_PASSWORD_HASHES: dict[str, str] = {}


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
    """
    if role not in _TEST_PASSWORD_HASHES:
        _TEST_PASSWORD_HASHES[role] = auth_service.hash_password(
            "test-password-12chars",
        )
    return _TEST_PASSWORD_HASHES[role]


def _make_test_user(
    *,
    role: HumanRole = HumanRole.CEO,
    must_change_password: bool = False,
    user_id: str = _TEST_USER_ID,
    username: str = _TEST_USERNAME,
) -> User:
    """Create a test User with given role."""
    now = datetime.now(UTC)
    auth_service = _make_test_auth_service()
    return User(
        id=user_id,
        username=username,
        password_hash=_get_test_password_hash(role.value, auth_service),
        role=role,
        must_change_password=must_change_password,
        created_at=now,
        updated_at=now,
    )


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
# ``test_client`` fixture is function-scoped, though, and performs
# per-test clearing/reconnection.  The shared app uses
# ``_skip_lifecycle_shutdown=True`` to prevent lifespan shutdown from
# stopping/disconnecting shared services.  Tests that create their
# own apps may disconnect/stop the shared persistence/bus, but
# ``test_client`` re-connects them before each test.


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
        strategy=DisabledTrustStrategy(),
        config=TrustConfig(),
    )


@pytest.fixture(scope="session")
def coordination_metrics_store() -> CoordinationMetricsStore:
    return CoordinationMetricsStore()


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
    provider_health_tracker: ProviderHealthTracker,
    tool_invocation_tracker: ToolInvocationTracker,
    delegation_record_store: DelegationRecordStore,
    audit_log: AuditLog,
    trust_service: TrustService,
    coordination_metrics_store: CoordinationMetricsStore,
    event_stream_hub: EventStreamHub,
    interrupt_store: InterruptStore,
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
        config=root_config,
    )

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
        provider_health_tracker=provider_health_tracker,
        tool_invocation_tracker=tool_invocation_tracker,
        delegation_record_store=delegation_record_store,
        artifact_storage=FakeArtifactStorage(),
        audit_log=audit_log,
        trust_service=trust_service,
        coordination_metrics_store=coordination_metrics_store,
        event_stream_hub=event_stream_hub,
        interrupt_store=interrupt_store,
        _skip_lifecycle_shutdown=True,
    )


# ── Function-scoped test_client with per-test reset ────────────


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


@pytest.fixture
def test_client(  # noqa: C901, PLR0912, PLR0913, PLR0915
    _shared_app: Litestar,  # noqa: PT019
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
) -> Iterator[TestClient[Any]]:
    """Yield a TestClient wrapping the shared app with clean state.

    The expensive ``create_app()`` runs once per worker.  Each test
    gets a function-scoped ``TestClient`` whose lifespan startup
    re-runs (idempotent, ~90ms) but shutdown is skipped (saves
    ~460ms per test).
    """
    # 1. Clear all mutable service state and undo method patches
    _services = (
        cost_tracker,
        approval_store,
        performance_tracker,
        agent_registry,
        provider_health_tracker,
        tool_invocation_tracker,
        delegation_record_store,
        audit_log,
        coordination_metrics_store,
    )
    for svc in _services:
        # Restore original methods BEFORE calling clear(): a prior
        # test may have monkeypatched ``svc.clear`` itself (or a method
        # that clear() invokes) with a stub that raises or corrupts
        # state; we want the real ``clear`` implementation to run.
        _restore_instance_patches(svc)
        # AgentRegistryService.clear and ApprovalStore.clear are async
        # (#1599); use the sync test-only entry point so this sync
        # fixture keeps its shape.
        if isinstance(svc, (AgentRegistryService, ApprovalStore)):
            svc.reset_for_test_sync()
        else:
            svc.clear()
    fake_persistence.clear()
    fake_message_bus.clear()

    # 2. Re-connect persistence and reset task engine running state.
    #    A previous test may have disconnected persistence via its own
    #    app's lifespan shutdown.  The task engine's _running stays True
    #    because _skip_lifecycle_shutdown prevents stop(), but its
    #    internal asyncio tasks die when the previous TestClient's
    #    event loop closes.  Resetting _running lets the next startup
    #    create fresh tasks on the new event loop.
    fake_persistence._connected = True
    app_state: AppState = _shared_app.state.app_state
    # Reset the task engine for the new event loop.  The session-
    # scoped engine's queues accumulate pending-put/get futures bound
    # to the *previous* TestClient's event loop; once that loop
    # closes those futures are permanently unusable and block any
    # further async interaction with the queue.  Recreate the queues
    # and reset the running flag so the next startup creates fresh
    # processing tasks on the new loop.
    te = app_state._task_engine
    if te is not None:
        import asyncio as _aio

        te._running = False
        # asyncio.Queue (Python 3.10+) lazily binds to the running
        # event loop on first ``put``/``get``; constructing it here
        # in sync context does *not* bind it to this thread's loop,
        # so the next async consumer inside the new TestClient picks
        # up the fresh loop correctly.
        te._queue = _aio.Queue(maxsize=te._config.max_queue_size)
        te._observer_queue = _aio.Queue(
            maxsize=te._config.effective_observer_queue_size,
        )
        te._versions = type(te._versions)()
        te._observers.clear()

    # 3. Clear AppState-internal stores and caches.
    #    Session and lockout stores are kept (rebuilding them from
    #    DB on every test is expensive); we clear their in-memory
    #    caches in place so revoked-session / lockout state from a
    #    prior test cannot bleed into the next one.  The
    #    has_session_store / has_lockout_store guards in
    #    _safe_startup() then correctly skip re-initialization.
    if app_state._session_store is not None:
        app_state._session_store._revoked.clear()
    if app_state._lockout_store is not None:
        # _locked is an internal cache on the concrete store; the
        # LockoutStore Protocol exposes only the public API.
        app_state._lockout_store._locked.clear()  # type: ignore[attr-defined]
    app_state._ticket_store._tickets.clear()
    app_state._user_presence._counts.clear()
    if app_state._interrupt_store is not None:
        app_state._interrupt_store._pending.clear()
        app_state._interrupt_store._events.clear()
        app_state._interrupt_store._results.clear()
    if app_state._event_stream_hub is not None:
        app_state._event_stream_hub._subscribers.clear()
    if app_state._settings_service is not None:
        app_state._settings_service._cache.clear()
    # Clear the escalation queue + pending-future registry so a prior
    # test's in-flight escalations cannot bleed into the next one.
    if app_state.escalation_store is not None:
        app_state.escalation_store._rows.clear()  # type: ignore[attr-defined]
    if app_state.escalation_registry is not None:
        app_state.escalation_registry._futures.clear()

    # Clear the per-op rate-limit sliding-window store so a prior
    # test's 429 buckets (e.g. ``setup.complete`` at 5/3600s) cannot
    # bleed into the next one.
    per_op_store = getattr(_shared_app.state, "per_op_rate_limit_store", None)
    if per_op_store is not None:
        buckets = getattr(per_op_store, "_buckets", None)
        if isinstance(buckets, dict):
            buckets.clear()
        locks = getattr(per_op_store, "_locks", None)
        if isinstance(locks, dict):
            locks.clear()

    # 4. Re-seed test users
    _seed_test_users(fake_persistence, auth_service)

    # 5. Snapshot AppState service refs before the test
    saved = {
        attr: getattr(app_state, attr)
        for attr in AppState.__slots__
        if attr.startswith("_")
    }

    # 6. Clear Litestar-internal rate-limit stores to prevent 429s
    #    from accumulating request counters across tests.  Reaches
    #    into private attributes (``stores._stores`` and
    #    ``store._store``) because Litestar has no public
    #    bulk-clear API.  Guarded with ``hasattr`` so a Litestar
    #    upgrade fails with a clear, actionable error instead of
    #    a cryptic ``AttributeError`` deep in fixture setup.
    _shared_stores = _shared_app.stores
    if not hasattr(_shared_stores, "_stores"):
        msg = (
            "Test fixture expected Litestar app.stores to expose a "
            "private '_stores' mapping for rate-limit reset, but it "
            "was not found. Litestar internals may have changed; "
            "update this fixture to use a supported store-clearing "
            "API if available."
        )
        raise RuntimeError(msg)
    for store in _shared_stores._stores.values():
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

    # 7. Enter TestClient -- startup re-runs (idempotent),
    #    shutdown is skipped (_skip_lifecycle_shutdown=True).
    #    Startup may create a system user (ensure_system_user) and
    #    modify settings, so re-clear persistence + settings cache
    #    and re-seed users AFTER entering.
    with TestClient(_shared_app) as client:
        fake_persistence.clear()
        fake_persistence._connected = True
        if app_state._settings_service is not None:
            app_state._settings_service._cache.clear()
        _seed_test_users(fake_persistence, auth_service)
        _promote_first_owner(fake_persistence)
        client.headers.update(make_auth_headers("ceo"))
        yield client

    # 8. Restore AppState refs (undo test mutations)
    for attr, value in saved.items():
        setattr(app_state, attr, value)


def _promote_first_owner(backend: FakePersistenceBackend) -> None:
    """Promote the first seeded user to OWNER.

    Replicates ``_maybe_promote_first_owner`` from the lifespan
    startup.  Called after seeding test users to ensure at least
    one user has ``OrgRole.OWNER``, matching the production
    startup behavior.
    """
    from synthorg.api.auth.models import OrgRole

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
    from synthorg.core.enums import TaskType

    if assigned_to is None and status in {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.COMPLETED,
    }:
        assigned_to = "alice"
    return Task(
        id=task_id,
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
        id=approval_id,
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
