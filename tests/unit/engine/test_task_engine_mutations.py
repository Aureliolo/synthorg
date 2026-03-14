"""CRUD mutation, typed error, and consistency tests for TaskEngine."""

from typing import TYPE_CHECKING

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.errors import (
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import (
    CancelTaskMutation,
    CreateTaskMutation,
    TransitionTaskMutation,
    UpdateTaskMutation,
)
from tests.unit.engine.task_engine_helpers import (
    FakePersistence,
    FakeTaskRepository,
    make_create_data,
)

if TYPE_CHECKING:
    from synthorg.engine.task_engine_config import TaskEngineConfig

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
            make_create_data(title="My Task"),
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
            task_data=make_create_data(),
        )
        result = await engine.submit(mutation)
        assert result.success is True
        assert result.version == 1

    async def test_create_with_assignee(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            make_create_data(assigned_to=None),
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
            make_create_data(title="Original"),
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
            make_create_data(),
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
            make_create_data(),
            requested_by="alice",
        )
        assigned, _ = await engine.transition_task(
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
            make_create_data(),
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
            make_create_data(),
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
            make_create_data(),
            requested_by="alice",
        )
        assigned, _ = await engine.transition_task(
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
            make_create_data(),
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
            make_create_data(title="Findme"),
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
            make_create_data(project="proj-a"),
            requested_by="alice",
        )
        await engine.create_task(
            make_create_data(project="proj-b"),
            requested_by="alice",
        )
        all_tasks, all_total = await engine.list_tasks()
        assert len(all_tasks) == 2
        assert all_total == 2

        filtered, filtered_total = await engine.list_tasks(project="proj-a")
        assert len(filtered) == 1
        assert filtered_total == 1

    async def test_list_tasks_by_status(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Assigning",
            assigned_to="bob",
        )

        created, _ = await engine.list_tasks(status=TaskStatus.CREATED)
        assigned, _ = await engine.list_tasks(status=TaskStatus.ASSIGNED)
        assert len(created) == 0
        assert len(assigned) == 1


# ── Read-through error wrapping ────────────────────────────────


@pytest.mark.unit
class TestReadThroughErrorWrapping:
    """Persistence errors in read-through methods raise TaskInternalError."""

    async def test_get_task_wraps_persistence_error(
        self,
        persistence: FakePersistence,
    ) -> None:
        from synthorg.engine.errors import TaskInternalError

        async def exploding_get(task_id: str) -> None:
            msg = "disk I/O"
            raise OSError(msg)

        persistence.tasks.get = exploding_get  # type: ignore[method-assign]
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        try:
            with pytest.raises(TaskInternalError, match="Failed to read task"):
                await eng.get_task("task-1")
        finally:
            await eng.stop(timeout=2.0)

    async def test_list_tasks_wraps_persistence_error(
        self,
        persistence: FakePersistence,
    ) -> None:
        from synthorg.engine.errors import TaskInternalError

        async def exploding_list(**kwargs: object) -> None:
            msg = "connection refused"
            raise ConnectionError(msg)

        persistence.tasks.list_tasks = exploding_list  # type: ignore[assignment,method-assign]
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        try:
            with pytest.raises(TaskInternalError, match="Failed to list tasks"):
                await eng.list_tasks()
        finally:
            await eng.stop(timeout=2.0)

    async def test_get_task_lets_memory_error_propagate(
        self,
        persistence: FakePersistence,
    ) -> None:
        async def oom_get(task_id: str) -> None:
            raise MemoryError

        persistence.tasks.get = oom_get  # type: ignore[method-assign]
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        try:
            with pytest.raises(MemoryError):
                await eng.get_task("task-1")
        finally:
            await eng.stop(timeout=2.0)

    async def test_list_tasks_lets_memory_error_propagate(
        self,
        persistence: FakePersistence,
    ) -> None:
        async def oom_list(**kwargs: object) -> None:
            raise MemoryError

        persistence.tasks.list_tasks = oom_list  # type: ignore[assignment,method-assign]
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        try:
            with pytest.raises(MemoryError):
                await eng.list_tasks()
        finally:
            await eng.stop(timeout=2.0)


# ── list_tasks safety cap ─────────────────────────────────────


@pytest.mark.unit
class TestListTasksSafetyCap:
    """list_tasks caps results at _MAX_LIST_RESULTS."""

    async def test_results_capped_at_max(
        self,
        persistence: FakePersistence,
    ) -> None:
        """When persistence returns more than cap, result is truncated."""

        original_list = persistence.tasks.list_tasks

        async def oversized_list(**kwargs: object) -> tuple[Task, ...]:
            # Return a few real tasks, then monkey-patch to simulate > cap
            result = await original_list(**kwargs)  # type: ignore[arg-type]
            # Create a list longer than cap by repeating
            return result * 20_000 if result else result

        persistence.tasks.list_tasks = oversized_list  # type: ignore[method-assign]
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        try:
            # Create one task so the oversized list has data to repeat
            await eng.create_task(make_create_data(), requested_by="alice")
            tasks, total = await eng.list_tasks()
            assert len(tasks) <= 10_000
            assert total == 20_000
        finally:
            await eng.stop(timeout=2.0)


# ── Cancel not found ─────────────────────────────────────────


@pytest.mark.unit
class TestCancelNotFound:
    """Cancel mutation on a non-existent task."""

    async def test_cancel_not_found(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskNotFoundError, match="not found"):
            await engine.cancel_task(
                "task-nonexistent",
                requested_by="alice",
                reason="test",
            )


# ── Previous status in results ────────────────────────────────


@pytest.mark.unit
class TestPreviousStatus:
    """Verify previous_status is populated in mutation results."""

    async def test_create_has_no_previous_status(
        self,
        engine: TaskEngine,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(),
        )
        result = await engine.submit(mutation)
        assert result.success is True
        assert result.previous_status is None

    async def test_transition_has_previous_status(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=task.id,
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "bob"},
        )
        result = await engine.submit(mutation)
        assert result.success is True
        assert result.previous_status == TaskStatus.CREATED

    async def test_cancel_has_previous_status(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        # First move to ASSIGNED so cancel is valid
        await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Assigning",
            assigned_to="bob",
        )
        mutation = CancelTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=task.id,
            reason="No longer needed",
        )
        result = await engine.submit(mutation)
        assert result.success is True
        assert result.previous_status == TaskStatus.ASSIGNED


# ── Immutable field rejection ─────────────────────────────────


@pytest.mark.unit
class TestImmutableFieldRejection:
    """UpdateTaskMutation and TransitionTaskMutation reject immutable fields."""

    def test_update_rejects_status(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="immutable"):
            UpdateTaskMutation(
                request_id="req-1",
                requested_by="alice",
                task_id="task-1",
                updates={"status": "completed"},
            )

    def test_update_rejects_id(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="immutable"):
            UpdateTaskMutation(
                request_id="req-1",
                requested_by="alice",
                task_id="task-1",
                updates={"id": "new-id"},
            )

    def test_transition_rejects_id_override(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="immutable"):
            TransitionTaskMutation(
                request_id="req-1",
                requested_by="alice",
                task_id="task-1",
                target_status=TaskStatus.ASSIGNED,
                reason="test",
                overrides={"id": "new-id"},
            )


# ── Typed error propagation ──────────────────────────────────


@pytest.mark.unit
class TestTypedErrors:
    """Convenience methods raise typed errors."""

    async def test_update_not_found_raises_typed(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskNotFoundError):
            await engine.update_task(
                "task-nonexistent",
                {"title": "X"},
                requested_by="alice",
            )

    async def test_delete_not_found_raises_typed(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskNotFoundError):
            await engine.delete_task(
                "task-nonexistent",
                requested_by="alice",
            )

    async def test_transition_not_found_raises_typed(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskNotFoundError):
            await engine.transition_task(
                "task-nonexistent",
                TaskStatus.ASSIGNED,
                requested_by="alice",
                reason="test",
            )

    async def test_update_version_conflict_raises_typed(
        self,
        engine: TaskEngine,
    ) -> None:
        """Version conflict via convenience method raises TaskVersionConflictError."""
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        with pytest.raises(TaskVersionConflictError, match="conflict"):
            await engine.update_task(
                task.id,
                {"title": "changed"},
                requested_by="alice",
                expected_version=99,
            )


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
                task_data=make_create_data(),
            )
            result = await eng.submit(mutation)
            assert result.success is False
            assert result.error == "Internal error processing mutation"
        finally:
            await eng.stop(timeout=2.0)


# ── TaskMutationResult consistency ────────────────────────────


@pytest.mark.unit
class TestMutationResultConsistency:
    """Verify _check_consistency validator on TaskMutationResult."""

    def test_success_with_error_rejected(self) -> None:
        """Successful result must not carry an error."""
        from pydantic import ValidationError

        from synthorg.engine.task_engine_models import TaskMutationResult

        with pytest.raises(ValidationError, match="error"):
            TaskMutationResult(
                request_id="r",
                success=True,
                error="oops",
            )

    def test_failure_without_error_rejected(self) -> None:
        """Failed result must carry an error description."""
        from pydantic import ValidationError

        from synthorg.engine.task_engine_models import TaskMutationResult

        with pytest.raises(ValidationError, match="error"):
            TaskMutationResult(
                request_id="r",
                success=False,
            )
