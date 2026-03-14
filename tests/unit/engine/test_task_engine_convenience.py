"""Convenience method, typed error dispatch, and lifecycle edge-case tests.

Split from ``test_task_engine_extended.py`` to keep files under 800 lines.
"""

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.errors import (
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import (
    CreateTaskMutation,
    TaskMutationResult,
)
from tests.unit.engine.task_engine_helpers import (
    FakePersistence,
    make_create_data,
)

# ── Typed error dispatch for all error codes ─────────────────


@pytest.mark.unit
class TestRaiseTypedErrorAllCodes:
    """_raise_typed_error maps all error_code values to typed exceptions."""

    def test_not_found_code(self) -> None:
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="not found",
            error_code="not_found",
        )
        with pytest.raises(TaskNotFoundError, match="not found"):
            TaskEngine._raise_typed_error(result)

    def test_version_conflict_code(self) -> None:
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="conflict",
            error_code="version_conflict",
        )
        with pytest.raises(TaskVersionConflictError, match="conflict"):
            TaskEngine._raise_typed_error(result)

    def test_internal_code(self) -> None:
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="boom",
            error_code="internal",
        )
        with pytest.raises(TaskInternalError, match="boom"):
            TaskEngine._raise_typed_error(result)

    def test_validation_code_falls_through(self) -> None:
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="bad data",
            error_code="validation",
        )
        with pytest.raises(TaskMutationError, match="bad data"):
            TaskEngine._raise_typed_error(result)

    def test_validation_code_raises_mutation_error(self) -> None:
        """Validation error_code falls through to generic TaskMutationError."""
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="generic",
            error_code="validation",
        )
        with pytest.raises(TaskMutationError, match="generic"):
            TaskEngine._raise_typed_error(result)

    def test_default_error_message_when_error_is_empty(self) -> None:
        """Empty error string triggers the 'Mutation failed' default."""
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="",
            error_code="validation",
        )
        with pytest.raises(TaskMutationError, match="Mutation failed"):
            TaskEngine._raise_typed_error(result)


# ── Transition with overrides via engine ─────────────────────


@pytest.mark.unit
class TestTransitionOverridesViaEngine:
    """Transition overrides flow through the engine correctly."""

    async def test_transition_with_assigned_to_override(
        self,
        engine: TaskEngine,
    ) -> None:
        """assigned_to passed as kwarg becomes an override on the mutation."""
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        transitioned, prev = await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Assigning",
            assigned_to="bob",
        )
        assert transitioned.assigned_to == "bob"
        assert prev == TaskStatus.CREATED

    async def test_transition_returns_previous_status(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        # CREATED -> ASSIGNED
        assigned, prev1 = await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Assigning",
            assigned_to="bob",
        )
        assert prev1 == TaskStatus.CREATED

        # ASSIGNED -> IN_PROGRESS
        in_progress, prev2 = await engine.transition_task(
            assigned.id,
            TaskStatus.IN_PROGRESS,
            requested_by="bob",
            reason="Starting work",
        )
        assert prev2 == TaskStatus.ASSIGNED
        assert in_progress.status == TaskStatus.IN_PROGRESS


# ── PydanticValidationError wrapping in convenience methods ──


@pytest.mark.unit
class TestConvenienceMethodValidationWrapping:
    """Convenience methods wrap PydanticValidationError as TaskMutationError."""

    async def test_update_task_wraps_pydantic_validation(
        self,
        engine: TaskEngine,
    ) -> None:
        """UpdateTaskMutation with immutable field raises TaskMutationError."""
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        # 'id' is an immutable field rejected by model_validator
        with pytest.raises(TaskMutationError):
            await engine.update_task(
                task.id,
                {"id": "hacked"},
                requested_by="alice",
            )

    async def test_transition_task_wraps_pydantic_validation(
        self,
        engine: TaskEngine,
    ) -> None:
        """TransitionTaskMutation with blank reason raises TaskMutationError."""
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        # Blank requested_by should trigger NotBlankStr validation
        with pytest.raises(TaskMutationError):
            await engine.transition_task(
                task.id,
                TaskStatus.ASSIGNED,
                requested_by="   ",
                reason="Assigning",
                assigned_to="bob",
            )


# ── Version conflict via convenience methods ─────────────────


@pytest.mark.unit
class TestVersionConflictViaConvenienceMethods:
    """Convenience methods raise TaskVersionConflictError on version mismatch."""

    async def test_update_task_version_conflict(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        with pytest.raises(TaskVersionConflictError):
            await engine.update_task(
                task.id,
                {"title": "New title"},
                requested_by="alice",
                expected_version=99,
            )

    async def test_transition_task_version_conflict(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        with pytest.raises(TaskVersionConflictError):
            await engine.transition_task(
                task.id,
                TaskStatus.ASSIGNED,
                requested_by="alice",
                reason="Assigning",
                expected_version=99,
                assigned_to="bob",
            )


# ── Cancel task not found ────────────────────────────────────


@pytest.mark.unit
class TestCancelTaskNotFound:
    """cancel_task raises TaskNotFoundError for missing tasks."""

    async def test_cancel_nonexistent_raises_not_found(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskNotFoundError):
            await engine.cancel_task(
                "task-nonexistent",
                requested_by="alice",
                reason="Cleanup",
            )


# ── Delete task not found ────────────────────────────────────


@pytest.mark.unit
class TestDeleteTaskNotFound:
    """delete_task raises TaskNotFoundError for missing tasks."""

    async def test_delete_nonexistent_raises_not_found(
        self,
        engine: TaskEngine,
    ) -> None:
        with pytest.raises(TaskNotFoundError):
            await engine.delete_task(
                "task-nonexistent",
                requested_by="alice",
            )


# ── Start when already running ────────────────────────────────


@pytest.mark.unit
class TestStartAlreadyRunning:
    """Starting an already-running engine raises RuntimeError."""

    async def test_double_start_raises(
        self,
        engine: TaskEngine,
    ) -> None:
        # engine fixture already called start()
        with pytest.raises(RuntimeError, match="already running"):
            engine.start()


# ── Stop idempotency ─────────────────────────────────────────


@pytest.mark.unit
class TestStopIdempotency:
    """Stopping an already-stopped engine is a no-op."""

    async def test_stop_when_not_running(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        # Never started — stop should be safe
        await eng.stop(timeout=1.0)

    async def test_double_stop(
        self,
        engine: TaskEngine,
    ) -> None:
        await engine.stop(timeout=2.0)
        # Second stop is a no-op
        await engine.stop(timeout=1.0)


# ── Submit when not running ───────────────────────────────────


@pytest.mark.unit
class TestSubmitWhenNotRunning:
    """Submitting to a stopped engine raises TaskEngineNotRunningError."""

    async def test_submit_after_stop(
        self,
        engine: TaskEngine,
    ) -> None:
        from synthorg.engine.errors import TaskEngineNotRunningError

        await engine.stop(timeout=2.0)
        mutation = CreateTaskMutation(
            request_id="req-late",
            requested_by="alice",
            task_data=make_create_data(),
        )
        with pytest.raises(TaskEngineNotRunningError):
            await engine.submit(mutation)


# ── is_running property ──────────────────────────────────────


@pytest.mark.unit
class TestIsRunningProperty:
    """is_running reflects engine lifecycle."""

    async def test_running_after_start(
        self,
        engine: TaskEngine,
    ) -> None:
        assert engine.is_running is True

    async def test_not_running_after_stop(
        self,
        engine: TaskEngine,
    ) -> None:
        await engine.stop(timeout=2.0)
        assert engine.is_running is False

    async def test_not_running_before_start(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        assert eng.is_running is False
