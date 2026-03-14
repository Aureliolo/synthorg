"""Shared fakes and helpers for TaskEngine tests."""

import copy
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

    async def save(self, task: Task) -> None:
        self._tasks[task.id] = copy.deepcopy(task)

    async def get(self, task_id: str) -> Task | None:
        task = self._tasks.get(task_id)
        return copy.deepcopy(task) if task is not None else None

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

    async def publish(self, message: object) -> None:
        self.published.append(message)


class FailingMessageBus(FakeMessageBus):
    """Message bus that always fails on publish."""

    async def publish(self, message: object) -> None:
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
