"""Shared fixtures for API unit tests."""

import asyncio
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auth.config import AuthConfig
from synthorg.api.auth.models import ApiKey, User
from synthorg.api.auth.service import AuthService
from synthorg.api.guards import HumanRole
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.communication.channel import Channel
from synthorg.communication.message import Message
from synthorg.config.schema import RootConfig
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
    TaskStatus,
)
from synthorg.core.task import Task
from synthorg.engine.checkpoint.models import Checkpoint, Heartbeat
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)
from synthorg.persistence.errors import DuplicateRecordError, QueryError
from synthorg.security.models import AuditEntry, AuditVerdictStr
from synthorg.security.timeout.parked_context import ParkedContext

# ── Test auth constants ───────────────────────────────────────

_TEST_JWT_SECRET = "test-secret-that-is-at-least-32-characters-long"
_TEST_USER_ID = "test-user-001"
_TEST_USERNAME = "testadmin"

# ── Fake Repositories ────────────────────────────────────────────


class FakeTaskRepository:
    """In-memory task repository for tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def save(self, task: Task) -> None:
        self._tasks[task.id] = task

    async def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
    ) -> tuple[Task, ...]:
        result = list(self._tasks.values())
        if status is not None:
            result = [t for t in result if t.status == status]
        if assigned_to is not None:
            result = [t for t in result if t.assigned_to == assigned_to]
        if project is not None:
            result = [t for t in result if t.project == project]
        return tuple(result)

    async def delete(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None


class FakeCostRecordRepository:
    """In-memory cost record repository for tests."""

    def __init__(self) -> None:
        self._records: list[CostRecord] = []

    async def save(self, record: CostRecord) -> None:
        self._records.append(record)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[CostRecord, ...]:
        result = self._records
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if task_id is not None:
            result = [r for r in result if r.task_id == task_id]
        return tuple(result)

    async def aggregate(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> float:
        records = await self.query(agent_id=agent_id, task_id=task_id)
        return sum(r.cost_usd for r in records)


class FakeMessageRepository:
    """In-memory message repository for tests."""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    async def save(self, message: Message) -> None:
        self._messages.append(message)

    async def get_history(
        self,
        channel: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        result = [m for m in self._messages if m.channel == channel]
        if limit is not None and limit > 0:
            result = result[-limit:]
        return tuple(result)


# ── Fake Persistence Backend ────────────────────────────────────


class FakeLifecycleEventRepository:
    """In-memory lifecycle event repository for tests."""

    def __init__(self) -> None:
        self._events: list[AgentLifecycleEvent] = []

    async def save(self, event: AgentLifecycleEvent) -> None:
        self._events.append(event)

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        event_type: LifecycleEventType | None = None,
        since: datetime | None = None,
    ) -> tuple[AgentLifecycleEvent, ...]:
        result = self._events
        if agent_id is not None:
            result = [e for e in result if e.agent_id == agent_id]
        if event_type is not None:
            result = [e for e in result if e.event_type == event_type]
        if since is not None:
            result = [e for e in result if e.timestamp >= since]
        return tuple(result)


class FakeTaskMetricRepository:
    """In-memory task metric repository for tests."""

    def __init__(self) -> None:
        self._records: list[TaskMetricRecord] = []

    async def save(self, record: TaskMetricRecord) -> None:
        self._records.append(record)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[TaskMetricRecord, ...]:
        result = self._records
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if since is not None:
            result = [r for r in result if r.completed_at >= since]
        if until is not None:
            result = [r for r in result if r.completed_at <= until]
        return tuple(result)


class FakeCollaborationMetricRepository:
    """In-memory collaboration metric repository for tests."""

    def __init__(self) -> None:
        self._records: list[CollaborationMetricRecord] = []

    async def save(self, record: CollaborationMetricRecord) -> None:
        self._records.append(record)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        since: datetime | None = None,
    ) -> tuple[CollaborationMetricRecord, ...]:
        result = self._records
        if agent_id is not None:
            result = [r for r in result if r.agent_id == agent_id]
        if since is not None:
            result = [r for r in result if r.recorded_at >= since]
        return tuple(result)


class FakeParkedContextRepository:
    """In-memory parked context repository for tests."""

    def __init__(self) -> None:
        self._contexts: dict[str, ParkedContext] = {}

    async def save(self, context: ParkedContext) -> None:
        self._contexts[context.id] = context

    async def get(self, parked_id: str) -> ParkedContext | None:
        return self._contexts.get(parked_id)

    async def get_by_approval(self, approval_id: str) -> ParkedContext | None:
        for ctx in self._contexts.values():
            if ctx.approval_id == approval_id:
                return ctx
        return None

    async def get_by_agent(self, agent_id: str) -> tuple[ParkedContext, ...]:
        return tuple(ctx for ctx in self._contexts.values() if ctx.agent_id == agent_id)

    async def delete(self, parked_id: str) -> bool:
        return self._contexts.pop(parked_id, None) is not None


class FakeAuditRepository:
    """In-memory audit entry repository for tests."""

    def __init__(self) -> None:
        self._entries: dict[str, AuditEntry] = {}

    async def save(self, entry: AuditEntry) -> None:
        if entry.id in self._entries:
            msg = f"Duplicate audit entry {entry.id!r}"
            raise DuplicateRecordError(msg)
        self._entries[entry.id] = entry

    async def query(  # noqa: PLR0913
        self,
        *,
        agent_id: str | None = None,
        action_type: str | None = None,
        verdict: AuditVerdictStr | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> tuple[AuditEntry, ...]:
        if limit < 1:
            msg = "limit must be >= 1"
            raise QueryError(msg)
        results = sorted(
            self._entries.values(),
            key=lambda e: e.timestamp,
            reverse=True,
        )
        if agent_id is not None:
            results = [e for e in results if e.agent_id == agent_id]
        if action_type is not None:
            results = [e for e in results if e.action_type == action_type]
        if verdict is not None:
            results = [e for e in results if e.verdict == verdict]
        if risk_level is not None:
            results = [e for e in results if e.risk_level == risk_level]
        if since is not None:
            results = [e for e in results if e.timestamp >= since]
        if until is not None:
            results = [e for e in results if e.timestamp <= until]
        return tuple(results[:limit])


class FakeUserRepository:
    """In-memory user repository for tests."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    async def save(self, user: User) -> None:
        self._users[user.id] = user

    async def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    async def get_by_username(self, username: str) -> User | None:
        for user in self._users.values():
            if user.username == username:
                return user
        return None

    async def list_users(self) -> tuple[User, ...]:
        return tuple(self._users.values())

    async def count(self) -> int:
        return len(self._users)

    async def delete(self, user_id: str) -> bool:
        return self._users.pop(user_id, None) is not None


class FakeApiKeyRepository:
    """In-memory API key repository for tests."""

    def __init__(self) -> None:
        self._keys: dict[str, ApiKey] = {}

    async def save(self, key: ApiKey) -> None:
        self._keys[key.id] = key

    async def get(self, key_id: str) -> ApiKey | None:
        return self._keys.get(key_id)

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        for key in self._keys.values():
            if key.key_hash == key_hash:
                return key
        return None

    async def list_by_user(self, user_id: str) -> tuple[ApiKey, ...]:
        return tuple(k for k in self._keys.values() if k.user_id == user_id)

    async def delete(self, key_id: str) -> bool:
        return self._keys.pop(key_id, None) is not None


class FakeCheckpointRepository:
    """In-memory checkpoint repository for tests."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, Checkpoint] = {}

    async def save(self, checkpoint: Checkpoint) -> None:
        self._checkpoints[checkpoint.id] = checkpoint

    async def get_latest(
        self,
        *,
        execution_id: str | None = None,
        task_id: str | None = None,
    ) -> Checkpoint | None:
        if execution_id is None and task_id is None:
            msg = "At least one of execution_id or task_id is required"
            raise ValueError(msg)
        candidates = list(self._checkpoints.values())
        if execution_id is not None:
            candidates = [c for c in candidates if c.execution_id == execution_id]
        if task_id is not None:
            candidates = [c for c in candidates if c.task_id == task_id]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.turn_number)

    async def delete_by_execution(self, execution_id: str) -> int:
        to_delete = [
            k for k, v in self._checkpoints.items() if v.execution_id == execution_id
        ]
        for k in to_delete:
            del self._checkpoints[k]
        return len(to_delete)


class FakeHeartbeatRepository:
    """In-memory heartbeat repository for tests."""

    def __init__(self) -> None:
        self._heartbeats: dict[str, Heartbeat] = {}

    async def save(self, heartbeat: Heartbeat) -> None:
        self._heartbeats[heartbeat.execution_id] = heartbeat

    async def get(self, execution_id: str) -> Heartbeat | None:
        return self._heartbeats.get(execution_id)

    async def get_stale(self, threshold: datetime) -> tuple[Heartbeat, ...]:
        stale = [
            h for h in self._heartbeats.values() if h.last_heartbeat_at < threshold
        ]
        stale.sort(key=lambda h: h.last_heartbeat_at)
        return tuple(stale)

    async def delete(self, execution_id: str) -> bool:
        return self._heartbeats.pop(execution_id, None) is not None


class FakePersistenceBackend:
    """In-memory persistence backend for tests."""

    def __init__(self) -> None:
        self._tasks = FakeTaskRepository()
        self._cost_records = FakeCostRecordRepository()
        self._messages = FakeMessageRepository()
        self._lifecycle_events = FakeLifecycleEventRepository()
        self._task_metrics = FakeTaskMetricRepository()
        self._collaboration_metrics = FakeCollaborationMetricRepository()
        self._parked_contexts = FakeParkedContextRepository()
        self._audit_entries = FakeAuditRepository()
        self._users = FakeUserRepository()
        self._api_keys = FakeApiKeyRepository()
        self._checkpoints = FakeCheckpointRepository()
        self._heartbeats_repo = FakeHeartbeatRepository()
        self._settings: dict[str, str] = {}
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def health_check(self) -> bool:
        return self._connected

    async def migrate(self) -> None:
        pass

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def backend_name(self) -> str:
        return "fake"

    @property
    def tasks(self) -> FakeTaskRepository:
        return self._tasks

    @property
    def cost_records(self) -> FakeCostRecordRepository:
        return self._cost_records

    @property
    def messages(self) -> FakeMessageRepository:
        return self._messages

    @property
    def lifecycle_events(self) -> FakeLifecycleEventRepository:
        return self._lifecycle_events

    @property
    def task_metrics(self) -> FakeTaskMetricRepository:
        return self._task_metrics

    @property
    def collaboration_metrics(self) -> FakeCollaborationMetricRepository:
        return self._collaboration_metrics

    @property
    def parked_contexts(self) -> FakeParkedContextRepository:
        return self._parked_contexts

    @property
    def audit_entries(self) -> FakeAuditRepository:
        return self._audit_entries

    @property
    def users(self) -> FakeUserRepository:
        return self._users

    @property
    def api_keys(self) -> FakeApiKeyRepository:
        return self._api_keys

    @property
    def checkpoints(self) -> FakeCheckpointRepository:
        return self._checkpoints

    @property
    def heartbeats(self) -> FakeHeartbeatRepository:
        return self._heartbeats_repo

    async def get_setting(self, key: str) -> str | None:
        return self._settings.get(key)

    async def set_setting(self, key: str, value: str) -> None:
        self._settings[key] = value


# ── Fake Message Bus ────────────────────────────────────────────


class FakeMessageBus:
    """In-memory message bus for tests."""

    def __init__(self) -> None:
        self._running = False
        self._channels: list[Channel] = []

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def publish(self, message: Message) -> None:
        pass

    async def send_direct(self, message: Message, *, recipient: str) -> None:
        pass

    async def subscribe(self, channel_name: str, subscriber_id: str) -> Any:
        return None

    async def unsubscribe(self, channel_name: str, subscriber_id: str) -> None:
        pass

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> Any:
        # Simulate waiting for a message (yields to event loop)
        if timeout is not None:
            await asyncio.sleep(min(timeout, 0.01))
        return None

    async def create_channel(self, channel: Channel) -> Channel:
        self._channels.append(channel)
        return channel

    async def get_channel(self, channel_name: str) -> Channel:
        for ch in self._channels:
            if ch.name == channel_name:
                return ch
        msg = f"Channel {channel_name!r} not found"
        raise ValueError(msg)

    async def list_channels(self) -> tuple[Channel, ...]:
        return tuple(self._channels)

    async def get_channel_history(
        self,
        channel_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        return ()


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
    token, _ = auth_service.create_token(user)
    return {"Authorization": f"Bearer {token}"}


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def auth_config() -> AuthConfig:
    return _make_test_auth_config()


@pytest.fixture
def auth_service() -> AuthService:
    return _make_test_auth_service()


@pytest.fixture
async def fake_persistence() -> FakePersistenceBackend:
    backend = FakePersistenceBackend()
    await backend.connect()
    return backend


@pytest.fixture
async def fake_message_bus() -> FakeMessageBus:
    bus = FakeMessageBus()
    await bus.start()
    return bus


@pytest.fixture
def cost_tracker() -> CostTracker:
    return CostTracker()


@pytest.fixture
def approval_store() -> ApprovalStore:
    return ApprovalStore()


@pytest.fixture
def root_config() -> RootConfig:
    return RootConfig(company_name="test-company")


@pytest.fixture
def fake_task_engine(
    fake_persistence: FakePersistenceBackend,
) -> TaskEngine:
    """TaskEngine backed by the shared fake persistence."""
    return TaskEngine(
        persistence=fake_persistence,
    )


@pytest.fixture
def test_client(  # noqa: PLR0913
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    cost_tracker: CostTracker,
    approval_store: ApprovalStore,
    root_config: RootConfig,
    auth_service: AuthService,
    fake_task_engine: TaskEngine,
) -> Generator[TestClient[Any]]:
    # Pre-seed users for each role so JWT sub claims resolve
    _seed_test_users(fake_persistence, auth_service)

    app = create_app(
        config=root_config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=cost_tracker,
        approval_store=approval_store,
        auth_service=auth_service,
        task_engine=fake_task_engine,
    )
    with TestClient(app) as client:
        # Default: CEO token (most tests need write access)
        client.headers.update(make_auth_headers("ceo"))
        yield client


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
