"""Shared fixtures for API unit tests."""

from typing import Any

import pytest
from litestar.testing import TestClient

from ai_company.api.app import create_app
from ai_company.budget.cost_record import CostRecord  # noqa: TC001
from ai_company.budget.tracker import CostTracker
from ai_company.communication.channel import Channel  # noqa: TC001
from ai_company.communication.message import Message  # noqa: TC001
from ai_company.config.schema import RootConfig
from ai_company.core.enums import TaskStatus
from ai_company.core.task import Task

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


class FakePersistenceBackend:
    """In-memory persistence backend for tests."""

    def __init__(self) -> None:
        self._tasks = FakeTaskRepository()
        self._cost_records = FakeCostRecordRepository()
        self._messages = FakeMessageRepository()
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


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def fake_persistence() -> FakePersistenceBackend:
    backend = FakePersistenceBackend()
    backend._connected = True
    return backend


@pytest.fixture
def fake_message_bus() -> FakeMessageBus:
    bus = FakeMessageBus()
    bus._running = True
    return bus


@pytest.fixture
def cost_tracker() -> CostTracker:
    return CostTracker()


@pytest.fixture
def root_config() -> RootConfig:
    return RootConfig(company_name="test-company")


@pytest.fixture
def test_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    cost_tracker: CostTracker,
    root_config: RootConfig,
) -> TestClient[Any]:
    app = create_app(
        config=root_config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=cost_tracker,
    )
    return TestClient(app)


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
    from ai_company.core.enums import TaskType

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
