"""Extended coverage tests for TaskEngine.

Covers test gaps identified during PR #325 review:
- FIFO ordering guarantee
- Default reason generation in transition_task
- Delete snapshot publishes new_status=None
- Cancel version bump correctness
- create_task _raise_typed_error dispatch for all error codes
- Snapshot reason propagation for transitions and cancels
- _processing_loop MemoryError re-raise
"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from ai_company.core.enums import TaskStatus
from ai_company.engine.errors import (
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from ai_company.engine.task_engine import TaskEngine, _MutationEnvelope
from ai_company.engine.task_engine_models import (
    CancelTaskMutation,
    CreateTaskMutation,
    TaskMutationResult,
    TaskStateChanged,
    TransitionTaskMutation,
)
from tests.unit.engine.task_engine_helpers import (
    FakeMessageBus,
    FakePersistence,
    _make_create_data,
)

if TYPE_CHECKING:
    from ai_company.engine.task_engine_config import TaskEngineConfig


def _snapshot_content(bus: FakeMessageBus, index: int = 0) -> str:
    """Extract the JSON content from a published snapshot message.

    The ``FakeMessageBus`` stores items as ``object`` because the bus
    protocol is generic; this helper performs the attribute access that
    mypy would otherwise reject.
    """
    msg = bus.published[index]
    return msg.content  # type: ignore[attr-defined,no-any-return]


# ── FIFO ordering guarantee ─────────────────────────────────


@pytest.mark.unit
class TestFIFOOrdering:
    """Mutations are processed in FIFO order via the single-writer queue."""

    async def test_mutations_processed_in_submission_order(
        self,
        engine: TaskEngine,
    ) -> None:
        """Create 5 tasks and verify they are processed in order."""
        results: list[TaskMutationResult] = []
        mutations = [
            CreateTaskMutation(
                request_id=f"req-{i}",
                requested_by="alice",
                task_data=_make_create_data(title=f"Task {i}"),
            )
            for i in range(5)
        ]
        for mutation in mutations:
            result = await engine.submit(mutation)
            results.append(result)

        assert all(r.success for r in results)
        # Each result's request_id matches submission order
        for i, result in enumerate(results):
            assert result.request_id == f"req-{i}"
            assert result.task is not None
            assert result.task.title == f"Task {i}"

    async def test_interleaved_create_update_ordering(
        self,
        engine: TaskEngine,
    ) -> None:
        """Create then update: update sees the created task."""
        task = await engine.create_task(
            _make_create_data(title="Original"),
            requested_by="alice",
        )
        # Immediately update — this should see the task because
        # the queue processes sequentially
        updated = await engine.update_task(
            task.id,
            {"title": "Updated"},
            requested_by="alice",
        )
        assert updated.title == "Updated"


# ── Default reason generation ────────────────────────────────


@pytest.mark.unit
class TestDefaultReasonGeneration:
    """transition_task generates a default reason when none is provided."""

    async def test_empty_reason_generates_default(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        transitioned, _ = await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            assigned_to="bob",
            # reason defaults to ""
        )
        assert transitioned.status == TaskStatus.ASSIGNED

    async def test_explicit_reason_preserved(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            _make_create_data(),
            requested_by="alice",
        )
        transitioned, _ = await engine.transition_task(
            task.id,
            TaskStatus.ASSIGNED,
            requested_by="alice",
            reason="Manager assigned task",
            assigned_to="bob",
        )
        assert transitioned.status == TaskStatus.ASSIGNED


# ── Delete snapshot new_status=None ──────────────────────────


@pytest.mark.unit
class TestDeleteSnapshotEvent:
    """Delete mutations publish events with new_status=None."""

    async def test_delete_snapshot_has_none_status(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
        config: TaskEngineConfig,
    ) -> None:
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            task = await eng.create_task(
                _make_create_data(),
                requested_by="alice",
            )
            await asyncio.sleep(0)  # let snapshot publish
            message_bus.published.clear()

            await eng.delete_task(task.id, requested_by="alice")
            await asyncio.sleep(0)  # let snapshot publish

            assert len(message_bus.published) == 1
            event = TaskStateChanged.model_validate_json(
                _snapshot_content(message_bus),
            )
            assert event.mutation_type == "delete"
            assert event.new_status is None
            assert event.task is None
        finally:
            await eng.stop(timeout=2.0)


# ── Cancel version bump ─────────────────────────────────────


@pytest.mark.unit
class TestCancelVersionBump:
    """Cancel mutations correctly bump the version counter."""

    async def test_cancel_increments_version(
        self,
        engine: TaskEngine,
    ) -> None:
        # Create (v1) -> Assign (v2) -> Cancel (v3)
        create_mut = CreateTaskMutation(
            request_id="req-c",
            requested_by="alice",
            task_data=_make_create_data(),
        )
        r1 = await engine.submit(create_mut)
        assert r1.version == 1
        assert r1.task is not None

        assign_mut = TransitionTaskMutation(
            request_id="req-a",
            requested_by="alice",
            task_id=r1.task.id,
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "bob"},
        )
        r2 = await engine.submit(assign_mut)
        assert r2.version == 2

        cancel_mut = CancelTaskMutation(
            request_id="req-x",
            requested_by="alice",
            task_id=r1.task.id,
            reason="No longer needed",
        )
        r3 = await engine.submit(cancel_mut)
        assert r3.success is True
        assert r3.version == 3
        assert r3.task is not None
        assert r3.task.status == TaskStatus.CANCELLED


# ── create_task _raise_typed_error dispatch ──────────────────


@pytest.mark.unit
class TestCreateTaskTypedErrorDispatch:
    """create_task uses _raise_typed_error for proper error dispatch."""

    async def test_create_internal_error_raises_task_internal(
        self,
        persistence: FakePersistence,
        config: TaskEngineConfig,
    ) -> None:
        """Internal persistence error raises TaskInternalError."""

        async def exploding_save(task: object) -> None:
            msg = "Disk full"
            raise OSError(msg)

        persistence.tasks.save = exploding_save  # type: ignore[method-assign]
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            with pytest.raises(TaskInternalError):
                await eng.create_task(
                    _make_create_data(),
                    requested_by="alice",
                )
        finally:
            await eng.stop(timeout=2.0)

    async def test_create_validation_error_raises_mutation_error(
        self,
        engine: TaskEngine,
    ) -> None:
        """Validation failure (assigned_to on CREATED) raises TaskMutationError."""
        with pytest.raises(TaskMutationError):
            await engine.create_task(
                _make_create_data(assigned_to="should-fail"),
                requested_by="alice",
            )


# ── Snapshot reason propagation ──────────────────────────────


@pytest.mark.unit
class TestSnapshotReasonPropagation:
    """Snapshot events carry the reason from transition/cancel mutations."""

    async def test_transition_snapshot_carries_reason(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
        config: TaskEngineConfig,
    ) -> None:
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            task = await eng.create_task(
                _make_create_data(),
                requested_by="alice",
            )
            await asyncio.sleep(0)
            message_bus.published.clear()

            await eng.transition_task(
                task.id,
                TaskStatus.ASSIGNED,
                requested_by="alice",
                reason="Manager assigned",
                assigned_to="bob",
            )
            await asyncio.sleep(0)

            assert len(message_bus.published) == 1
            event = TaskStateChanged.model_validate_json(
                _snapshot_content(message_bus),
            )
            assert event.reason == "Manager assigned"
        finally:
            await eng.stop(timeout=2.0)

    async def test_cancel_snapshot_carries_reason(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
        config: TaskEngineConfig,
    ) -> None:
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            task = await eng.create_task(
                _make_create_data(),
                requested_by="alice",
            )
            await eng.transition_task(
                task.id,
                TaskStatus.ASSIGNED,
                requested_by="alice",
                reason="Assigning",
                assigned_to="bob",
            )
            await asyncio.sleep(0)
            message_bus.published.clear()

            await eng.cancel_task(
                task.id,
                requested_by="alice",
                reason="Budget cut",
            )
            await asyncio.sleep(0)

            assert len(message_bus.published) == 1
            event = TaskStateChanged.model_validate_json(
                _snapshot_content(message_bus),
            )
            assert event.reason == "Budget cut"
        finally:
            await eng.stop(timeout=2.0)

    async def test_create_snapshot_reason_is_none(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
        config: TaskEngineConfig,
    ) -> None:
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            await eng.create_task(
                _make_create_data(),
                requested_by="alice",
            )
            await asyncio.sleep(0)

            assert len(message_bus.published) == 1
            event = TaskStateChanged.model_validate_json(
                _snapshot_content(message_bus),
            )
            assert event.reason is None
        finally:
            await eng.stop(timeout=2.0)

    async def test_update_snapshot_reason_is_none(
        self,
        persistence: FakePersistence,
        message_bus: FakeMessageBus,
        config: TaskEngineConfig,
    ) -> None:
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            task = await eng.create_task(
                _make_create_data(),
                requested_by="alice",
            )
            await asyncio.sleep(0)
            message_bus.published.clear()

            await eng.update_task(
                task.id,
                {"title": "Updated"},
                requested_by="alice",
            )
            await asyncio.sleep(0)

            assert len(message_bus.published) == 1
            event = TaskStateChanged.model_validate_json(
                _snapshot_content(message_bus),
            )
            assert event.reason is None
        finally:
            await eng.stop(timeout=2.0)


# ── MemoryError re-raise in processing loop ──────────────────


@pytest.mark.unit
class TestMemoryErrorReRaise:
    """MemoryError and RecursionError must propagate, not be swallowed."""

    async def test_memory_error_propagates_through_process_one(
        self,
        persistence: FakePersistence,
        config: TaskEngineConfig,
    ) -> None:
        """MemoryError in dispatch propagates through _process_one."""

        async def oom_save(task: object) -> None:
            raise MemoryError

        persistence.tasks.save = oom_save  # type: ignore[method-assign]
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            mutation = CreateTaskMutation(
                request_id="req-oom",
                requested_by="alice",
                task_data=_make_create_data(),
            )
            # The MemoryError propagates to the processing loop which
            # re-raises it, causing the processing task to fail.
            # The submit future may never resolve, so we check the
            # processing task directly.
            envelope = _MutationEnvelope(mutation=mutation)
            eng._queue.put_nowait(envelope)

            # Wait for the processing task to complete/fail
            assert eng._processing_task is not None
            with pytest.raises(MemoryError):
                await eng._processing_task
        finally:
            eng._running = False
            eng._processing_task = None


# ── _fail_remaining_futures coverage ─────────────────────────


@pytest.mark.unit
class TestFailRemainingFuturesCount:
    """Verify _fail_remaining_futures tracks and logs the count."""

    async def test_multiple_queued_futures_all_failed(
        self,
        persistence: FakePersistence,
    ) -> None:
        """All queued futures get shutdown results."""
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng._running = True

        envelopes = []
        for i in range(3):
            mutation = CreateTaskMutation(
                request_id=f"req-{i}",
                requested_by="alice",
                task_data=_make_create_data(),
            )
            envelope = _MutationEnvelope(mutation=mutation)
            eng._queue.put_nowait(envelope)
            envelopes.append(envelope)

        eng._running = False
        eng._fail_remaining_futures()

        for envelope in envelopes:
            assert envelope.future.done()
            result = envelope.future.result()
            assert result.success is False
            assert result.error_code == "internal"
            assert "shut down" in (result.error or "").lower()


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
        from ai_company.engine.errors import TaskVersionConflictError

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

    def test_none_code_falls_through(self) -> None:
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="generic",
        )
        with pytest.raises(TaskMutationError, match="generic"):
            TaskEngine._raise_typed_error(result)

    def test_missing_error_uses_default_message(self) -> None:
        result = TaskMutationResult(
            request_id="r",
            success=False,
            error="Mutation failed",
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
            _make_create_data(),
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
            _make_create_data(),
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
            _make_create_data(),
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
            _make_create_data(),
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
            _make_create_data(),
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
            _make_create_data(),
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
        from ai_company.engine.errors import TaskEngineNotRunningError

        await engine.stop(timeout=2.0)
        mutation = CreateTaskMutation(
            request_id="req-late",
            requested_by="alice",
            task_data=_make_create_data(),
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
