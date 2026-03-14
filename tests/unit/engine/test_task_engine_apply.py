"""Unit tests for task_engine_apply dispatch and apply functions."""

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.task_engine_apply import (
    apply_cancel,
    apply_create,
    apply_delete,
    apply_transition,
    apply_update,
    dispatch,
)
from synthorg.engine.task_engine_models import (
    CancelTaskMutation,
    CreateTaskMutation,
    DeleteTaskMutation,
    TaskMutationResult,
    TransitionTaskMutation,
    UpdateTaskMutation,
)
from synthorg.engine.task_engine_version import VersionTracker
from tests.unit.engine.task_engine_helpers import FakePersistence, make_create_data


@pytest.fixture
def persistence() -> FakePersistence:
    return FakePersistence()


@pytest.fixture
def versions() -> VersionTracker:
    return VersionTracker()


# ── Dispatch routing ─────────────────────────────────────────


@pytest.mark.unit
class TestDispatch:
    """Tests for mutation dispatch routing."""

    async def test_dispatch_create(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(),
        )
        result = await dispatch(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None

    async def test_dispatch_unknown_type_raises(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """Unknown mutation type raises TypeError."""

        class FakeMutation:
            mutation_type = "fake"
            request_id = "req-1"
            requested_by = "alice"

        with pytest.raises(TypeError, match="Unknown mutation type"):
            await dispatch(FakeMutation(), persistence, versions)  # type: ignore[arg-type]


# ── apply_create ─────────────────────────────────────────────


@pytest.mark.unit
class TestApplyCreate:
    """Tests for task creation apply logic."""

    async def test_creates_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(title="New Task"),
        )
        result = await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.title == "New Task"
        assert result.task.id.startswith("task-")
        assert result.version == 1

    async def test_create_validation_error(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """Invalid task data returns failure with validation error code.

        assigned_to is valid for CreateTaskData but Task rejects it
        when status is CREATED (assignment consistency invariant).
        """
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(assigned_to="bob"),
        )
        result = await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "validation"
        assert "Invalid task data" in (result.error or "")

    async def test_create_persists_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(),
        )
        result = await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.task is not None
        stored = await persistence.tasks.get(result.task.id)
        assert stored is not None


# ── apply_update ─────────────────────────────────────────────


@pytest.mark.unit
class TestApplyUpdate:
    """Tests for task update apply logic."""

    async def _create_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> TaskMutationResult:
        mutation = CreateTaskMutation(
            request_id="req-c",
            requested_by="alice",
            task_data=make_create_data(),
        )
        return await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]

    async def test_update_fields(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=created.task.id,
            updates={"title": "Updated"},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.title == "Updated"
        assert result.version == 2

    async def test_update_not_found(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-nonexistent",
            updates={"title": "X"},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "not_found"

    async def test_update_version_conflict(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=created.task.id,
            updates={"title": "X"},
            expected_version=99,
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "version_conflict"

    async def test_update_empty_no_op(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=created.task.id,
            updates={},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.title == created.task.title

    async def test_update_validation_error(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """Invalid update data returns failure with validation error code."""
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=created.task.id,
            updates={"priority": "bogus_priority"},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "validation"

    async def test_update_records_previous_status(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=created.task.id,
            updates={"title": "New"},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.previous_status == TaskStatus.CREATED


# ── apply_transition ─────────────────────────────────────────


@pytest.mark.unit
class TestApplyTransition:
    """Tests for task status transition apply logic."""

    async def _create_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> TaskMutationResult:
        mutation = CreateTaskMutation(
            request_id="req-c",
            requested_by="alice",
            task_data=make_create_data(),
        )
        return await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]

    async def test_valid_transition(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=created.task.id,
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "bob"},
        )
        result = await apply_transition(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.status == TaskStatus.ASSIGNED
        assert result.previous_status == TaskStatus.CREATED

    async def test_transition_not_found(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-nonexistent",
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
        )
        result = await apply_transition(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "not_found"

    async def test_transition_version_conflict(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=created.task.id,
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "bob"},
            expected_version=99,
        )
        result = await apply_transition(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "version_conflict"

    async def test_invalid_transition(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """CREATED -> COMPLETED is not valid."""
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=created.task.id,
            target_status=TaskStatus.COMPLETED,
            reason="skip",
            overrides={"assigned_to": "bob"},
        )
        result = await apply_transition(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "validation"


# ── apply_delete ─────────────────────────────────────────────


@pytest.mark.unit
class TestApplyDelete:
    """Tests for task deletion apply logic."""

    async def test_delete_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert create_result.task is not None
        mutation = DeleteTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=create_result.task.id,
        )
        result = await apply_delete(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.version == 0

        stored = await persistence.tasks.get(create_result.task.id)
        assert stored is None

    async def test_delete_not_found(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = DeleteTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-nonexistent",
        )
        result = await apply_delete(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "not_found"

    async def test_delete_removes_version_tracking(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert create_result.task is not None
        task_id = create_result.task.id
        assert versions.get(task_id) == 1

        await apply_delete(
            DeleteTaskMutation(
                request_id="req-d",
                requested_by="alice",
                task_id=task_id,
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert versions.get(task_id) == 0


# ── apply_cancel ─────────────────────────────────────────────


@pytest.mark.unit
class TestApplyCancel:
    """Tests for task cancellation apply logic."""

    async def _create_and_assign(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> str:
        """Create a task and transition to ASSIGNED, return task_id."""
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert create_result.task is not None
        task_id = create_result.task.id
        await apply_transition(
            TransitionTaskMutation(
                request_id="req-t",
                requested_by="alice",
                task_id=task_id,
                target_status=TaskStatus.ASSIGNED,
                reason="Assign",
                overrides={"assigned_to": "bob"},
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        return task_id

    async def test_cancel_assigned_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        task_id = await self._create_and_assign(persistence, versions)
        mutation = CancelTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=task_id,
            reason="No longer needed",
        )
        result = await apply_cancel(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.status == TaskStatus.CANCELLED
        assert result.previous_status == TaskStatus.ASSIGNED

    async def test_cancel_not_found(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = CancelTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-nonexistent",
            reason="test",
        )
        result = await apply_cancel(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "not_found"

    async def test_cancel_invalid_status(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """CREATED -> CANCELLED is not a valid transition."""
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert create_result.task is not None
        mutation = CancelTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=create_result.task.id,
            reason="Oops",
        )
        result = await apply_cancel(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "validation"
