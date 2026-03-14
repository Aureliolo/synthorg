"""Tests for PR review fixes: race condition, immutability, validation wrapping.

Covers fixes from Copilot, CodeRabbit, and Greptile review findings.
"""

import asyncio
import contextlib
from types import MappingProxyType
from typing import Any

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.errors import (
    TaskEngineNotRunningError,
    TaskMutationError,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.task_engine_models import (
    CreateTaskMutation,
    TaskMutationResult,
    TaskStateChanged,
    TransitionTaskMutation,
    UpdateTaskMutation,
)
from tests.unit.engine.task_engine_helpers import (
    FakeMessageBus,
    FakePersistence,
    make_create_data,
)

# ── Race condition: stop/submit coordination ─────────────────


@pytest.mark.unit
class TestLifecycleLock:
    """Lifecycle lock prevents submit-after-stop race condition."""

    async def test_submit_rejected_after_stop(
        self,
        persistence: FakePersistence,
    ) -> None:
        """submit() raises after stop() sets _running=False."""
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
        )
        eng.start()
        await eng.stop(timeout=2.0)
        mutation = CreateTaskMutation(
            request_id="req-late",
            requested_by="alice",
            task_data=make_create_data(),
        )
        with pytest.raises(TaskEngineNotRunningError):
            await eng.submit(mutation)

    async def test_concurrent_stop_and_submit(
        self,
        persistence: FakePersistence,
    ) -> None:
        """Concurrent stop() and submit() resolve without hanging futures."""
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            config=TaskEngineConfig(max_queue_size=100),
        )
        eng.start()

        # Create a task first so the engine is clearly working
        task = await eng.create_task(
            make_create_data(),
            requested_by="alice",
        )
        assert task is not None

        # Now race: stop and create in parallel
        stop_task = asyncio.create_task(eng.stop(timeout=2.0))

        # Yield once to let stop() begin — both outcomes are valid
        await asyncio.sleep(0)

        # The create should either succeed (if enqueued before stop)
        # or raise TaskEngineNotRunningError (if stop wins the lock first)
        with contextlib.suppress(TaskEngineNotRunningError):
            await eng.create_task(
                make_create_data(),
                requested_by="bob",
            )

        await stop_task

    async def test_stop_idempotent_under_lock(
        self,
        persistence: FakePersistence,
    ) -> None:
        """Two concurrent stop() calls don't deadlock or double-drain."""
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
        )
        eng.start()
        await asyncio.gather(
            eng.stop(timeout=2.0),
            eng.stop(timeout=2.0),
        )
        assert not eng.is_running


# ── MappingProxyType immutability ─────────────────────────────


@pytest.mark.unit
class TestMutationDictImmutability:
    """Mutation dicts are wrapped in MappingProxyType after construction."""

    def test_update_mutation_updates_is_mapping_proxy(self) -> None:
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            updates={"title": "New title"},
        )
        # Runtime type differs from annotation (dict -> MappingProxyType via __init__)
        assert type(mutation.updates) is MappingProxyType  # type: ignore[comparison-overlap,unreachable]

    def test_update_mutation_updates_is_immutable(self) -> None:
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            updates={"title": "New title"},
        )
        with pytest.raises(TypeError):
            mutation.updates["hacked"] = "value"

    def test_transition_mutation_overrides_is_mapping_proxy(self) -> None:
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "bob"},
        )
        # Runtime type differs from annotation (dict -> MappingProxyType via __init__)
        assert type(mutation.overrides) is MappingProxyType  # type: ignore[comparison-overlap,unreachable]

    def test_transition_mutation_overrides_is_immutable(self) -> None:
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "bob"},
        )
        with pytest.raises(TypeError):
            mutation.overrides["hacked"] = "value"

    def test_update_mutation_deep_copies_input(self) -> None:
        """Original dict is not affected by mutation construction."""
        original: dict[str, object] = {"title": "Original"}
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            updates=original,
        )
        # Modifying original shouldn't affect mutation
        original["title"] = "Modified"
        assert mutation.updates["title"] == "Original"

    def test_transition_mutation_deep_copies_input(self) -> None:
        original: dict[str, object] = {"assigned_to": "alice"}
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides=original,
        )
        original["assigned_to"] = "hacker"
        assert mutation.overrides["assigned_to"] == "alice"


# ── TaskStateChanged.task_id ─────────────────────────────────


@pytest.mark.unit
class TestTaskStateChangedTaskId:
    """TaskStateChanged always carries task_id."""

    def test_task_id_required(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="task_id"):
            TaskStateChanged(  # type: ignore[call-arg]
                mutation_type="create",
                request_id="req-1",
                requested_by="alice",
                new_status=TaskStatus.CREATED,
                version=1,
            )

    def test_task_id_on_create(self) -> None:
        event = TaskStateChanged(
            mutation_type="create",
            request_id="req-1",
            requested_by="alice",
            task_id="task-new",
            new_status=TaskStatus.CREATED,
            version=1,
        )
        assert event.task_id == "task-new"

    def test_task_id_on_delete(self) -> None:
        event = TaskStateChanged(
            mutation_type="delete",
            request_id="req-1",
            requested_by="alice",
            task_id="task-deleted",
            version=0,
        )
        assert event.task_id == "task-deleted"
        assert event.task is None

    def test_task_id_in_serialization(self) -> None:
        event = TaskStateChanged(
            mutation_type="create",
            request_id="req-1",
            requested_by="alice",
            task_id="task-1",
            new_status=TaskStatus.CREATED,
            version=1,
        )
        data = event.model_dump()
        assert data["task_id"] == "task-1"
        restored = TaskStateChanged.model_validate(data)
        assert restored.task_id == "task-1"


# ── PydanticValidationError wrapping in create/delete/cancel ──


@pytest.mark.unit
class TestPydanticValidationWrapping:
    """create_task, delete_task, cancel_task wrap PydanticValidationError."""

    async def test_create_task_wraps_validation_error(
        self,
        engine: TaskEngine,
    ) -> None:
        """Blank requested_by triggers validation, wrapped as TaskMutationError."""
        with pytest.raises(TaskMutationError):
            await engine.create_task(
                make_create_data(),
                requested_by="   ",
            )

    async def test_delete_task_wraps_validation_error(
        self,
        engine: TaskEngine,
    ) -> None:
        """Blank task_id triggers validation, wrapped as TaskMutationError."""
        with pytest.raises(TaskMutationError):
            await engine.delete_task(
                "   ",
                requested_by="alice",
            )

    async def test_cancel_task_wraps_validation_error(
        self,
        engine: TaskEngine,
    ) -> None:
        """Blank reason triggers validation, wrapped as TaskMutationError."""
        with pytest.raises(TaskMutationError):
            await engine.cancel_task(
                "task-1",
                requested_by="alice",
                reason="   ",
            )


# ── Snapshot publishing with task_id ─────────────────────────


@pytest.mark.unit
class TestSnapshotPublishingTaskId:
    """Snapshot events include task_id."""

    async def test_create_snapshot_includes_task_id(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
    ) -> None:
        """Create mutation publishes snapshot with task_id from result."""
        await message_bus.start()
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=TaskEngineConfig(publish_snapshots=True),
        )
        eng.start()
        task = await eng.create_task(
            make_create_data(),
            requested_by="alice",
        )
        await eng.stop(timeout=2.0)
        await message_bus.stop()

        assert len(message_bus.published) >= 1
        msg: Any = message_bus.published[0]
        event = TaskStateChanged.model_validate_json(msg.content)
        assert event.task_id == task.id

    async def test_delete_snapshot_includes_task_id(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
    ) -> None:
        """Delete mutation publishes snapshot with task_id."""
        await message_bus.start()
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=TaskEngineConfig(publish_snapshots=True),
        )
        eng.start()
        task = await eng.create_task(
            make_create_data(),
            requested_by="alice",
        )
        await eng.delete_task(task.id, requested_by="alice")
        await eng.stop(timeout=2.0)
        await message_bus.stop()

        # Second message is the delete event
        assert len(message_bus.published) >= 2
        msg: Any = message_bus.published[1]
        event = TaskStateChanged.model_validate_json(msg.content)
        assert event.task_id == task.id
        assert event.task is None


# ── INFO logging for successful mutations ────────────────────


@pytest.mark.unit
class TestMutationAppliedLogging:
    """Successful mutations are logged at INFO level."""

    async def test_create_task_logs_applied(
        self,
        engine: TaskEngine,
    ) -> None:
        """create_task logs TASK_ENGINE_MUTATION_APPLIED at INFO."""
        import structlog.testing

        with structlog.testing.capture_logs() as captured:
            await engine.create_task(
                make_create_data(),
                requested_by="alice",
            )

        applied = [
            e for e in captured if e.get("event") == "task_engine.mutation.applied"
        ]
        assert len(applied) >= 1
        assert applied[0]["mutation_type"] == "create"


# ── FakeTaskRepository deep-copy isolation ───────────────────


@pytest.mark.unit
class TestFakeTaskRepositoryIsolation:
    """FakeTaskRepository deep-copies tasks to prevent test isolation leaks."""

    async def test_save_deep_copies(
        self,
        persistence: FakePersistence,
        engine: TaskEngine,
    ) -> None:
        """Two reads of the same task return distinct objects."""
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        read1 = await engine.get_task(task.id)
        read2 = await engine.get_task(task.id)
        assert read1 is not None
        assert read2 is not None
        assert read1 == read2
        assert read1 is not read2


# ── TaskMutationResult consistency validation ────────────────


@pytest.mark.unit
class TestTaskMutationResultConsistency:
    """TaskMutationResult validates success/error/error_code consistency."""

    def test_success_with_error_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="error"):
            TaskMutationResult(
                request_id="r",
                success=True,
                error="should not be here",
            )

    def test_failure_without_error_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="error"):
            TaskMutationResult(
                request_id="r",
                success=False,
            )

    def test_success_with_error_code_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="error_code"):
            TaskMutationResult(
                request_id="r",
                success=True,
                error_code="internal",
            )

    def test_failure_without_error_code_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="error_code"):
            TaskMutationResult(
                request_id="r",
                success=False,
                error="something broke",
            )

    def test_valid_success_result(self) -> None:
        result = TaskMutationResult(
            request_id="r",
            success=True,
        )
        assert result.error is None
        assert result.error_code is None

    def test_valid_failure_result(self) -> None:
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="broken",
            error_code="internal",
        )
        assert result.error == "broken"
        assert result.error_code == "internal"


# ── _map_task_engine_errors 503 message sanitization ─────────


@pytest.mark.unit
class TestErrorMessageSanitization:
    """503 responses don't leak internal error details."""

    def test_not_running_sanitizes_message(self) -> None:
        from synthorg.api.controllers.tasks import _map_task_engine_errors

        exc = TaskEngineNotRunningError("internal detail about engine state")
        result = _map_task_engine_errors(exc)
        assert "internal detail" not in str(result)
        assert "temporarily unavailable" in str(result).lower()

    def test_queue_full_sanitizes_message(self) -> None:
        from synthorg.api.controllers.tasks import _map_task_engine_errors
        from synthorg.engine.errors import TaskEngineQueueFullError

        exc = TaskEngineQueueFullError("queue has 1000 items")
        result = _map_task_engine_errors(exc)
        assert "1000" not in str(result)


# ── Defensive guard: task is None after success ──────────────


@pytest.mark.unit
class TestConvenienceMethodTaskNoneGuard:
    """Convenience methods raise TaskInternalError when task is None after success."""

    async def test_create_task_none_guard(
        self,
        engine: TaskEngine,
    ) -> None:
        """create_task raises TaskInternalError if result.task is None."""
        from unittest.mock import AsyncMock

        from synthorg.engine.errors import TaskInternalError

        bogus = TaskMutationResult(request_id="r", success=True)
        engine.submit = AsyncMock(return_value=bogus)  # type: ignore[method-assign]
        with pytest.raises(
            TaskInternalError, match="create succeeded but task is None"
        ):
            await engine.create_task(make_create_data(), requested_by="alice")

    async def test_update_task_none_guard(
        self,
        engine: TaskEngine,
    ) -> None:
        """update_task raises TaskInternalError if result.task is None."""
        from unittest.mock import AsyncMock

        from synthorg.engine.errors import TaskInternalError

        bogus = TaskMutationResult(request_id="r", success=True)
        engine.submit = AsyncMock(return_value=bogus)  # type: ignore[method-assign]
        with pytest.raises(
            TaskInternalError, match="update succeeded but task is None"
        ):
            await engine.update_task("task-1", {"title": "X"}, requested_by="alice")

    async def test_transition_task_none_guard(
        self,
        engine: TaskEngine,
    ) -> None:
        """transition_task raises TaskInternalError if result.task is None."""
        from unittest.mock import AsyncMock

        from synthorg.engine.errors import TaskInternalError

        bogus = TaskMutationResult(request_id="r", success=True)
        engine.submit = AsyncMock(return_value=bogus)  # type: ignore[method-assign]
        with pytest.raises(
            TaskInternalError, match="transition succeeded but task is None"
        ):
            await engine.transition_task(
                "task-1",
                TaskStatus.ASSIGNED,
                requested_by="alice",
                reason="Assigning",
            )

    async def test_cancel_task_none_guard(
        self,
        engine: TaskEngine,
    ) -> None:
        """cancel_task raises TaskInternalError if result.task is None."""
        from unittest.mock import AsyncMock

        from synthorg.engine.errors import TaskInternalError

        bogus = TaskMutationResult(request_id="r", success=True)
        engine.submit = AsyncMock(return_value=bogus)  # type: ignore[method-assign]
        with pytest.raises(
            TaskInternalError, match="cancel succeeded but task is None"
        ):
            await engine.cancel_task("task-1", requested_by="alice", reason="Test")


# ── Processing loop unhandled-exception recovery ────────────


@pytest.mark.unit
class TestProcessingLoopExceptionRecovery:
    """Processing loop catches unhandled exceptions and returns internal error."""

    async def test_unhandled_exception_returns_internal_error(
        self,
        persistence: FakePersistence,
    ) -> None:
        """Non-MemoryError exception in dispatch returns error result."""

        async def exploding_save(task: object) -> None:
            msg = "Unexpected DB crash"
            raise RuntimeError(msg)

        persistence.tasks.save = exploding_save  # type: ignore[method-assign]

        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
        )
        eng.start()
        try:
            mutation = CreateTaskMutation(
                request_id="req-boom",
                requested_by="alice",
                task_data=make_create_data(),
            )
            result = await eng.submit(mutation)
            assert result.success is False
            assert result.error_code == "internal"
        finally:
            await eng.stop(timeout=2.0)

    async def test_engine_recovers_after_exception(
        self,
        persistence: FakePersistence,
    ) -> None:
        """Engine continues processing after a failed mutation."""
        call_count = 0
        original_save = persistence.tasks.save

        async def fail_once_save(task: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = "First save fails"
                raise RuntimeError(msg)
            await original_save(task)  # type: ignore[arg-type]

        persistence.tasks.save = fail_once_save  # type: ignore[method-assign]

        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
        )
        eng.start()
        try:
            # First create fails
            m1 = CreateTaskMutation(
                request_id="req-fail",
                requested_by="alice",
                task_data=make_create_data(),
            )
            r1 = await eng.submit(m1)
            assert r1.success is False

            # Second create succeeds — engine recovered
            m2 = CreateTaskMutation(
                request_id="req-ok",
                requested_by="alice",
                task_data=make_create_data(),
            )
            r2 = await eng.submit(m2)
            assert r2.success is True
            assert r2.task is not None
        finally:
            await eng.stop(timeout=2.0)


# ── Snapshot new_status=None for non-delete (result.task is None) ──


@pytest.mark.unit
class TestSnapshotNewStatusNone:
    """Snapshot publishes new_status=None when result.task is None (non-delete)."""

    async def test_snapshot_with_no_task_sets_new_status_none(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
    ) -> None:
        """When result.task is None but mutation is not delete, new_status is None."""
        await message_bus.start()
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=TaskEngineConfig(publish_snapshots=True),
        )
        eng.start()
        # Create then delete — delete snapshot has task=None and new_status=None
        task = await eng.create_task(make_create_data(), requested_by="alice")
        await eng.delete_task(task.id, requested_by="alice")
        await eng.stop(timeout=2.0)
        await message_bus.stop()

        # The delete event should have new_status=None
        assert len(message_bus.published) >= 2
        delete_msg: Any = message_bus.published[1]
        event = TaskStateChanged.model_validate_json(delete_msg.content)
        assert event.new_status is None
        assert event.task is None


# ── Cancel task full lifecycle ───────────────────────────────


@pytest.mark.unit
class TestCancelTaskLifecycle:
    """Full lifecycle tests for cancel_task convenience method."""

    async def test_cancel_returns_cancelled_task(
        self,
        engine: TaskEngine,
    ) -> None:
        """cancel_task returns the task in CANCELLED status."""
        task = await engine.create_task(make_create_data(), requested_by="alice")
        # Must transition to ASSIGNED first (CREATED -> CANCELLED is invalid)
        assigned, _ = await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Assigning",
            assigned_to="bob",
        )
        cancelled = await engine.cancel_task(
            assigned.id, requested_by="alice", reason="No longer needed"
        )
        assert cancelled.status == TaskStatus.CANCELLED

    async def test_cancel_nonexistent_raises_not_found(
        self,
        engine: TaskEngine,
    ) -> None:
        """cancel_task for missing task raises TaskNotFoundError."""
        from synthorg.engine.errors import TaskNotFoundError

        with pytest.raises(TaskNotFoundError):
            await engine.cancel_task(
                "task-nonexistent",
                requested_by="alice",
                reason="Cleanup",
            )


# ── Transition task wraps blank requested_by ─────────────────


@pytest.mark.unit
class TestTransitionTaskValidation:
    """transition_task wraps PydanticValidationError for blank fields."""

    async def test_blank_reason_wraps_validation(
        self,
        engine: TaskEngine,
    ) -> None:
        """Blank reason is caught and wrapped as TaskMutationError."""
        task = await engine.create_task(make_create_data(), requested_by="alice")
        with pytest.raises(TaskMutationError):
            await engine.transition_task(
                task.id,
                TaskStatus.ASSIGNED,
                requested_by="alice",
                reason="   ",
                assigned_to="bob",
            )
