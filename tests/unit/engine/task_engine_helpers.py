"""Shared fakes and helpers for TaskEngine tests."""

import copy
from collections.abc import Sequence
from typing import TYPE_CHECKING

from synthorg.core.task import Task
from synthorg.engine.task_engine_models import CreateTaskData

if TYPE_CHECKING:
    from synthorg.core.enums import TaskStatus

# ── Fakes ─────────────────────────────────────────────────────


class FakeTaskRepository:
    """Minimal in-memory task repository for engine tests.

    Deep-copies tasks on save/get to mirror real persistence
    behaviour and prevent test isolation regressions.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def save(self, entity: Task) -> None:
        self._tasks[entity.id] = copy.deepcopy(entity)

    async def get(self, entity_id: str) -> Task | None:
        task = self._tasks.get(entity_id)
        return copy.deepcopy(task) if task is not None else None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        result = sorted(self._tasks.values(), key=lambda t: t.id)
        sliced = result[offset : offset + limit]
        return tuple(copy.deepcopy(t) for t in sliced)

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        result = self._filtered(
            getattr(filter_spec, "status", None),
            getattr(filter_spec, "assigned_to", None),
            getattr(filter_spec, "project", None),
        )
        return tuple(result[offset : offset + limit])

    async def count(self, filter_spec: object) -> int:
        return len(
            self._filtered(
                getattr(filter_spec, "status", None),
                getattr(filter_spec, "assigned_to", None),
                getattr(filter_spec, "project", None),
            )
        )

    def _filtered(
        self,
        status: TaskStatus | None,
        assigned_to: str | None,
        project: str | None,
    ) -> list[Task]:
        result = sorted(self._tasks.values(), key=lambda t: t.id)
        if status is not None:
            result = [t for t in result if t.status == status]
        if assigned_to is not None:
            result = [t for t in result if t.assigned_to == assigned_to]
        if project is not None:
            result = [t for t in result if t.project == project]
        return [copy.deepcopy(t) for t in result]

    async def delete(self, entity_id: str) -> bool:
        return self._tasks.pop(entity_id, None) is not None


class FakePersistence:
    """Minimal fake persistence backend with only a task repository."""

    def __init__(self) -> None:
        self._tasks = FakeTaskRepository()

    @property
    def tasks(self) -> FakeTaskRepository:
        return self._tasks


class FakeMessageBus:
    """Minimal fake message bus that records published messages."""

    def __init__(self) -> None:
        self.published: list[object] = []
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def health_check(self) -> bool:
        return self._running

    async def publish(
        self,
        message: object,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        self.published.append(message)

    async def send_direct(
        self,
        message: object,
        *,
        recipient: str,
        ttl_seconds: float | None = None,
    ) -> None:
        pass

    async def publish_batch(
        self,
        messages: Sequence[object],
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        for msg in messages:
            self.published.append(msg)


class FailingMessageBus(FakeMessageBus):
    """Message bus that always fails on publish."""

    async def publish(
        self,
        message: object,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        msg = "Publish failed"
        raise RuntimeError(msg)


# ── Helpers ────────────────────────────────────────────────────


def make_create_data(**overrides: object) -> CreateTaskData:
    """Build a CreateTaskData with sensible defaults."""
    from synthorg.core.enums import TaskType

    defaults: dict[str, object] = {
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "test-project",
        "created_by": "test-agent",
    }
    defaults.update(overrides)
    return CreateTaskData(**defaults)  # type: ignore[arg-type]
