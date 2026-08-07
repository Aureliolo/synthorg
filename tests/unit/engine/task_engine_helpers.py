"""Shared fakes and helpers for TaskEngine tests."""

import copy
from collections.abc import Sequence
from typing import TYPE_CHECKING, override

from synthorg.core.plan import Plan
from synthorg.core.task import Task
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.plan_protocol import PlanFilterSpec

if TYPE_CHECKING:
    from synthorg.core.task_enums import TaskStatus

# ── Fakes ─────────────────────────────────────────────────────


class FakeTaskRepository:
    """Minimal in-memory task repository for engine tests.

    Deep-copies tasks on save/get to mirror real persistence
    behaviour and prevent test isolation regressions.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def save(self, entity: Task) -> None:
        self._tasks[str(entity.id)] = copy.deepcopy(entity)

    async def save_many(self, entities: tuple[Task, ...]) -> None:
        for entity in entities:
            self._tasks[str(entity.id)] = copy.deepcopy(entity)

    async def get(self, entity_id: str) -> Task | None:
        task = self._tasks.get(entity_id)
        return copy.deepcopy(task) if task is not None else None

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        result = sorted(self._tasks.values(), key=lambda t: t.id)
        sliced = result[offset : offset + limit]
        return tuple(copy.deepcopy(t) for t in sliced)

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
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


class FakePlanRepository:
    """In-memory plan repository, for the task-delete guard's lookup.

    Filters on ``parent_task_id`` because that is the one the guard
    asks: a fake that ignored it would report every task as free to
    delete and the guard's test would prove nothing.
    """

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}

    async def create(self, plan: Plan) -> None:
        self._plans[str(plan.id)] = copy.deepcopy(plan)

    async def update(self, plan: Plan, *, expected_version: int | None = None) -> None:
        self._plans[str(plan.id)] = copy.deepcopy(plan)

    async def save(self, entity: Plan, /) -> None:
        self._plans[str(entity.id)] = copy.deepcopy(entity)

    async def get(self, entity_id: str, /) -> Plan | None:
        plan = self._plans.get(entity_id)
        return copy.deepcopy(plan) if plan is not None else None

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        return await self.query(PlanFilterSpec(), limit=limit, offset=offset)

    def _matching(self, filter_spec: PlanFilterSpec) -> list[Plan]:
        return sorted(
            (
                copy.deepcopy(plan)
                for plan in self._plans.values()
                if filter_spec.parent_task_id in (None, plan.parent_task_id)
            ),
            key=lambda plan: str(plan.id),
        )

    async def query(
        self,
        filter_spec: PlanFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        return tuple(self._matching(filter_spec)[offset : offset + limit])

    async def count(self, filter_spec: PlanFilterSpec) -> int:
        return len(self._matching(filter_spec))

    async def delete(self, entity_id: str, /) -> bool:
        return self._plans.pop(entity_id, None) is not None


class FakePersistence:
    """Minimal fake persistence backend: tasks plus the plans the guard reads."""

    def __init__(self) -> None:
        self._tasks = FakeTaskRepository()
        self._plans = FakePlanRepository()

    @property
    def tasks(self) -> FakeTaskRepository:
        return self._tasks

    @property
    def plans(self) -> FakePlanRepository:
        return self._plans


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

    # The channel/subscription surface of the MessageBus protocol is not
    # exercised by the task-engine tests; stubs keep the fake in lockstep
    # with the protocol so a future engine call against one of these
    # surfaces fails loudly here instead of silently passing a stale fake.
    async def subscribe(self, channel_name: object, subscriber_id: object) -> object:
        raise NotImplementedError

    async def unsubscribe(self, channel_name: object, subscriber_id: object) -> None:
        raise NotImplementedError

    async def receive(
        self,
        channel_name: object,
        subscriber_id: object,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 -- mirrors bus protocol
    ) -> object:
        raise NotImplementedError

    async def create_channel(self, channel: object) -> object:
        raise NotImplementedError

    async def get_channel(self, channel_name: object) -> object:
        raise NotImplementedError

    async def list_channels(self) -> tuple[object, ...]:
        raise NotImplementedError

    async def get_channel_history(
        self,
        channel_name: object,
        *,
        limit: int | None = None,
    ) -> tuple[object, ...]:
        raise NotImplementedError

    def set_quadratic_alert_sink(self, sink: object) -> None:
        """No-op: this fake has no quadratic-fan-out enforcer."""


class FailingMessageBus(FakeMessageBus):
    """Message bus that always fails on publish."""

    @override
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
    from synthorg.core.task_enums import TaskType

    defaults: dict[str, object] = {
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "test-project",
        "created_by": "test-agent",
    }
    defaults.update(overrides)
    return CreateTaskData(**defaults)  # type: ignore[arg-type]
