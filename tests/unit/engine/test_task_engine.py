"""Tests for the centralized single-writer TaskEngine."""

import asyncio
from collections.abc import AsyncGenerator  # noqa: TC003

import pytest

from ai_company.core.enums import (
    TaskStatus,
    TaskType,
)
from ai_company.core.task import Task  # noqa: TC001
from ai_company.engine.errors import (
    TaskEngineNotRunningError,
    TaskMutationError,
)
from ai_company.engine.task_engine import TaskEngine
from ai_company.engine.task_engine_config import TaskEngineConfig
from ai_company.engine.task_engine_models import (
    CreateTaskData,
    CreateTaskMutation,
    DeleteTaskMutation,
    UpdateTaskMutation,
)

# ── Fakes ─────────────────────────────────────────────────────


class FakeTaskRepository:
    """Minimal in-memory task repository for engine tests."""

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


# ── Fixtures ──────────────────────────────────────────────────


def _make_create_data(**overrides: object) -> CreateTaskData:
    """Build a CreateTaskData with sensible defaults."""
    defaults: dict[str, object] = {
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "test-project",
        "created_by": "test-agent",
    }
    defaults.update(overrides)
    return CreateTaskData(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def persistence() -> FakePersistence:
    return FakePersistence()


@pytest.fixture
def message_bus() -> FakeMessageBus:
    return FakeMessageBus()


@pytest.fixture
def config() -> TaskEngineConfig:
    return TaskEngineConfig(max_queue_size=100)


@pytest.fixture
async def engine(
    persistence: FakePersistence,
    config: TaskEngineConfig,
) -> AsyncGenerator[TaskEngine]:
    """Create and start a TaskEngine, stop on teardown."""
    eng = TaskEngine(
        persistence=persistence,  # type: ignore[arg-type]
        config=config,
    )
    eng.start()
    yield eng
    await eng.stop(timeout=2.0)


@pytest.fixture
async def engine_with_bus(
    persistence: FakePersistence,
    message_bus: FakeMessageBus,
    config: TaskEngineConfig,
) -> AsyncGenerator[TaskEngine]:
    """Create and start a TaskEngine with a message bus."""
    eng = TaskEngine(
        persistence=persistence,  # type: ignore[arg-type]
        message_bus=message_bus,  # type: ignore[arg-type]
        config=config,
    )
    eng.start()
    yield eng
    await eng.stop(timeout=2.0)


# ── Lifecycle tests ───────────────────────────────────────────


@pytest.mark.unit
class TestTaskEngineLifecycle:
    """Tests for start/stop lifecycle."""

    async def test_start_sets_running(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        assert eng.is_running is False
        eng.start()
        assert eng.is_running is True
        await eng.stop(timeout=2.0)  # type: ignore[unreachable]
        assert eng.is_running is False

    async def test_double_start_raises(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        with pytest.raises(RuntimeError, match="already running"):
            eng.start()
        await eng.stop(timeout=2.0)

    async def test_stop_idempotent(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        await eng.stop(timeout=2.0)
        await eng.stop(timeout=2.0)  # no error

    async def test_restart(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        await eng.stop(timeout=2.0)
        eng.start()
        assert eng.is_running is True
        await eng.stop(timeout=2.0)


# ── Submit to stopped engine ──────────────────────────────────


@pytest.mark.unit
class TestSubmitToStoppedEngine:
    """Submitting to a stopped engine raises TaskEngineNotRunningError."""

    async def test_submit_raises(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=_make_create_data(),
        )
        with pytest.raises(TaskEngineNotRunningError):
            await eng.submit(mutation)


# ── Create mutation ───────────────────────────────────────────


@pytest.mark.unit
class TestCreateTask:
    """Tests for task creation via TaskEngine."""

    async def test_create_task(
        self,
        engine: TaskEngine,
        persistence: FakePersistence,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(title="My Task"),
            requested_by="alice",
        )
        assert task.title == "My Task"
        assert task.id.startswith("task-")
        assert task.status == TaskStatus.CREATED

        stored = await persistence.tasks.get(task.id)
        assert stored is not None
        assert stored.title == "My Task"

    async def test_create_returns_version_1(
        self,
        engine: TaskEngine,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=_make_create_data(),
        )
        result = await engine.submit(mutation)
        assert result.success is True
        assert result.version == 1

    async def test_create_with_assignee(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(assigned_to=None),
            requested_by="alice",
        )
        assert task.assigned_to is None


# ── Update mutation ───────────────────────────────────────────


@pytest.mark.unit
class TestUpdateTask:
    """Tests for task update via TaskEngine."""

    async def test_update_fields(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(title="Original"),
            requested_by="alice",
        )
        updated = await engine.update_task(
            task.id,
            {"title": "Updated"},
            requested_by="alice",
        )
        assert updated.title == "Updated"
        assert updated.id == task.id

    async def test_update_empty_no_op(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        result = await engine.update_task(
            task.id,
            {},
            requested_by="alice",
        )
        assert result.title == task.title

    async def test_update_not_found(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskMutationError, match="not found"):
            await engine.update_task(
                "task-nonexistent",
                {"title": "X"},
                requested_by="alice",
            )


# ── Transition mutation ───────────────────────────────────────


@pytest.mark.unit
class TestTransitionTask:
    """Tests for task status transitions via TaskEngine."""

    async def test_valid_transition(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        assigned = await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Assigning",
            assigned_to="bob",
        )
        assert assigned.status == TaskStatus.ASSIGNED
        assert assigned.assigned_to == "bob"

    async def test_invalid_transition(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        with pytest.raises(TaskMutationError):
            await engine.transition_task(
                task.id,
                TaskStatus.COMPLETED,
                requested_by="alice",
                reason="Skip to done",
                assigned_to="bob",
            )

    async def test_transition_not_found(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskMutationError, match="not found"):
            await engine.transition_task(
                "task-nonexistent",
                TaskStatus.ASSIGNED,
                requested_by="alice",
                reason="test",
            )


# ── Delete mutation ───────────────────────────────────────────


@pytest.mark.unit
class TestDeleteTask:
    """Tests for task deletion via TaskEngine."""

    async def test_delete_task(
        self,
        engine: TaskEngine,
        persistence: FakePersistence,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        deleted = await engine.delete_task(task.id, requested_by="alice")
        assert deleted is True

        stored = await persistence.tasks.get(task.id)
        assert stored is None

    async def test_delete_not_found(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskMutationError, match="not found"):
            await engine.delete_task(
                "task-nonexistent",
                requested_by="alice",
            )


# ── Cancel mutation ───────────────────────────────────────────


@pytest.mark.unit
class TestCancelTask:
    """Tests for task cancellation via TaskEngine."""

    async def test_cancel_assigned_task(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        assigned = await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Assigning",
            assigned_to="bob",
        )
        cancelled = await engine.cancel_task(
            assigned.id,
            requested_by="alice",
            reason="No longer needed",
        )
        assert cancelled.status == TaskStatus.CANCELLED

    async def test_cancel_from_created_fails(
        self,
        engine: TaskEngine,
    ) -> None:
        """CREATED -> CANCELLED is not a valid transition."""
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        with pytest.raises(TaskMutationError):
            await engine.cancel_task(
                task.id,
                requested_by="alice",
                reason="Oops",
            )


# ── Read-through ──────────────────────────────────────────────


@pytest.mark.unit
class TestReadThrough:
    """Tests for read-through methods that bypass the queue."""

    async def test_get_task(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(title="Findme"),
            requested_by="alice",
        )
        found = await engine.get_task(task.id)
        assert found is not None
        assert found.title == "Findme"

    async def test_get_task_not_found(
        self,
        engine: TaskEngine,
    ) -> None:
        result = await engine.get_task("task-nonexistent")
        assert result is None

    async def test_list_tasks(
        self,
        engine: TaskEngine,
    ) -> None:
        await engine.create_task(
            _make_create_data(project="proj-a"),
            requested_by="alice",
        )
        await engine.create_task(
            _make_create_data(project="proj-b"),
            requested_by="alice",
        )
        all_tasks = await engine.list_tasks()
        assert len(all_tasks) == 2

        filtered = await engine.list_tasks(project="proj-a")
        assert len(filtered) == 1

    async def test_list_tasks_by_status(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Assigning",
            assigned_to="bob",
        )

        created = await engine.list_tasks(status=TaskStatus.CREATED)
        assigned = await engine.list_tasks(status=TaskStatus.ASSIGNED)
        assert len(created) == 0
        assert len(assigned) == 1


# ── Version tracking ──────────────────────────────────────────


@pytest.mark.unit
class TestVersionTracking:
    """Tests for the in-memory version counter."""

    async def test_version_increments(
        self,
        engine: TaskEngine,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=_make_create_data(),
        )
        r1 = await engine.submit(mutation)
        assert r1.version == 1

        update = UpdateTaskMutation(
            request_id="req-2",
            requested_by="alice",
            task_id=r1.task.id,  # type: ignore[union-attr]
            updates={"title": "Updated"},
        )
        r2 = await engine.submit(update)
        assert r2.version == 2

    async def test_version_conflict(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        # version is 1 after create; expected_version=99 should fail
        update = UpdateTaskMutation(
            request_id="req-2",
            requested_by="alice",
            task_id=task.id,
            updates={"title": "X"},
            expected_version=99,
        )
        result = await engine.submit(update)
        assert result.success is False
        assert "conflict" in (result.error or "").lower()

    async def test_version_reset_on_delete(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        delete = DeleteTaskMutation(
            request_id="req-3",
            requested_by="alice",
            task_id=task.id,
        )
        result = await engine.submit(delete)
        assert result.version == 0


# ── Snapshot publishing ───────────────────────────────────────


@pytest.mark.unit
class TestSnapshotPublishing:
    """Tests for event publishing to the message bus."""

    async def test_snapshot_published_on_create(
        self,
        engine_with_bus: TaskEngine,
        message_bus: FakeMessageBus,
    ) -> None:
        await engine_with_bus.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        # Give the processing loop time to publish
        await asyncio.sleep(0.1)
        assert len(message_bus.published) == 1

    async def test_snapshot_publish_failure_does_not_affect_mutation(
        self,
        persistence: FakePersistence,
        config: TaskEngineConfig,
    ) -> None:
        failing_bus = FailingMessageBus()
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=failing_bus,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            task = await eng.create_task(
                _make_create_data(),
                requested_by="alice",
            )
            assert task.id.startswith("task-")

            stored = await persistence.tasks.get(task.id)
            assert stored is not None
        finally:
            await eng.stop(timeout=2.0)

    async def test_no_snapshot_when_disabled(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
    ) -> None:
        no_snap_config = TaskEngineConfig(publish_snapshots=False)
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=no_snap_config,
        )
        eng.start()
        try:
            await eng.create_task(
                _make_create_data(),
                requested_by="alice",
            )
            await asyncio.sleep(0.1)
            assert len(message_bus.published) == 0
        finally:
            await eng.stop(timeout=2.0)


# ── Sequential ordering ──────────────────────────────────────


@pytest.mark.unit
class TestSequentialOrdering:
    """Tests that mutations are processed sequentially."""

    async def test_concurrent_submits(
        self,
        engine: TaskEngine,
    ) -> None:
        """Multiple concurrent creates all succeed without interleaving."""
        tasks = await asyncio.gather(
            *(
                engine.create_task(
                    _make_create_data(title=f"Task {i}"),
                    requested_by="alice",
                )
                for i in range(10)
            ),
        )
        assert len(tasks) == 10
        ids = {t.id for t in tasks}
        assert len(ids) == 10  # all unique


# ── Drain on stop ─────────────────────────────────────────────


@pytest.mark.unit
class TestDrainOnStop:
    """Tests that stop() drains pending mutations."""

    async def test_pending_mutations_processed(
        self,
        persistence: FakePersistence,
    ) -> None:
        config = TaskEngineConfig(max_queue_size=100)
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()

        # Submit several mutations
        results = await asyncio.gather(
            *(
                eng.create_task(
                    _make_create_data(title=f"Drain {i}"),
                    requested_by="alice",
                )
                for i in range(5)
            ),
        )
        assert len(results) == 5

        await eng.stop(timeout=5.0)
        assert eng.is_running is False

        # All tasks should be persisted
        all_tasks = await persistence.tasks.list_tasks()
        assert len(all_tasks) == 5


# ── Queue full ────────────────────────────────────────────────


@pytest.mark.unit
class TestQueueFull:
    """Tests for queue full backpressure."""

    async def test_queue_full_raises(
        self,
        persistence: FakePersistence,
    ) -> None:
        tiny_config = TaskEngineConfig(max_queue_size=1)
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            config=tiny_config,
        )
        # Start the engine but pause the processing loop
        eng._running = True

        # First submit fills the queue
        mutation1 = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=_make_create_data(),
        )
        eng._queue.put_nowait(
            __import__(
                "ai_company.engine.task_engine",
                fromlist=["_MutationEnvelope"],
            )._MutationEnvelope(mutation=mutation1),
        )

        # Second submit should fail because queue is full
        mutation2 = CreateTaskMutation(
            request_id="req-2",
            requested_by="alice",
            task_data=_make_create_data(),
        )
        with pytest.raises(TaskEngineNotRunningError, match="queue is full"):
            await eng.submit(mutation2)

        eng._running = False


# ── Error propagation ────────────────────────────────────────


@pytest.mark.unit
class TestErrorPropagation:
    """Tests for error propagation via futures."""

    async def test_persistence_error_returns_failure(
        self,
        persistence: FakePersistence,
        config: TaskEngineConfig,
    ) -> None:
        """Persistence errors during mutation are captured in the result."""

        class FailingSaveRepo(FakeTaskRepository):
            async def save(self, task: Task) -> None:
                msg = "Disk full"
                raise OSError(msg)

        persistence._tasks = FailingSaveRepo()
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            mutation = CreateTaskMutation(
                request_id="req-1",
                requested_by="alice",
                task_data=_make_create_data(),
            )
            result = await eng.submit(mutation)
            assert result.success is False
            assert "Disk full" in (result.error or "")
        finally:
            await eng.stop(timeout=2.0)


# ── TaskEngineConfig ──────────────────────────────────────────


@pytest.mark.unit
class TestTaskEngineConfig:
    """Tests for TaskEngineConfig model."""

    def test_defaults(self) -> None:
        config = TaskEngineConfig()
        assert config.max_queue_size == 1000
        assert config.drain_timeout_seconds == 10.0
        assert config.publish_snapshots is True

    def test_custom_values(self) -> None:
        config = TaskEngineConfig(
            max_queue_size=500,
            drain_timeout_seconds=5.0,
            publish_snapshots=False,
        )
        assert config.max_queue_size == 500
        assert config.drain_timeout_seconds == 5.0
        assert config.publish_snapshots is False

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        config = TaskEngineConfig()
        with pytest.raises(ValidationError):
            config.max_queue_size = 999  # type: ignore[misc]
