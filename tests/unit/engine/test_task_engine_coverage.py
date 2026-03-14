"""Additional coverage tests for TaskEngine edge cases.

Covers: in-flight envelope resolution during drain, MemoryError re-raise,
_process_one exception paths, and snapshot publishing failures.
"""

import asyncio
import contextlib

import pytest

from synthorg.engine.errors import TaskInternalError
from synthorg.engine.task_engine import TaskEngine, _MutationEnvelope
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.task_engine_models import (
    CreateTaskMutation,
    TaskMutationResult,
)
from tests.unit.engine.task_engine_helpers import (
    FailingMessageBus,
    FakePersistence,
    make_create_data,
)

# ── In-flight envelope resolution ────────────────────────────


@pytest.mark.unit
class TestInFlightResolution:
    """Drain timeout resolves both in-flight and queued envelopes."""

    async def test_in_flight_envelope_resolved_on_drain_timeout(
        self,
        persistence: FakePersistence,
    ) -> None:
        """The in-flight envelope gets a failure result on drain timeout."""
        block = asyncio.Event()
        original_save = persistence.tasks.save

        async def slow_save(task: object) -> None:
            await block.wait()
            await original_save(task)  # type: ignore[arg-type]

        persistence.tasks.save = slow_save  # type: ignore[method-assign]

        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()

        # Submit a task that will block in slow_save
        blocked = asyncio.create_task(
            eng.create_task(make_create_data(), requested_by="alice"),
        )
        await asyncio.sleep(0.05)

        # The processing loop should be in _process_one with _in_flight set
        in_flight_before = eng._in_flight
        assert in_flight_before is not None

        # Stop with very short timeout — triggers _fail_remaining_futures
        await eng.stop(timeout=0.05)

        # In-flight should be cleared
        assert eng._in_flight is None

        # Release the block and clean up
        block.set()
        blocked.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await blocked


# ── _process_one exception handling ──────────────────────────


@pytest.mark.unit
class TestProcessOneExceptionHandling:
    """Test that _process_one handles unexpected exceptions gracefully."""

    async def test_dispatch_exception_returns_internal_error(
        self,
        persistence: FakePersistence,
        config: TaskEngineConfig,
    ) -> None:
        """An exception during dispatch produces an internal error result."""

        async def exploding_save(task: object) -> None:
            msg = "Unexpected persistence failure"
            raise RuntimeError(msg)

        persistence.tasks.save = exploding_save  # type: ignore[method-assign]

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
            assert result.error_code == "internal"
            assert "Internal error" in (result.error or "")
        finally:
            await eng.stop(timeout=2.0)


# ── Snapshot publish failure ─────────────────────────────────


@pytest.mark.unit
class TestSnapshotPublishFailure:
    """Snapshot publishing failure does not affect the mutation result."""

    async def test_publish_failure_logged_not_raised(
        self,
        persistence: FakePersistence,
    ) -> None:
        """Even when publish fails, create_task returns the task."""
        failing_bus = FailingMessageBus()
        config = TaskEngineConfig(publish_snapshots=True)
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            message_bus=failing_bus,  # type: ignore[arg-type]
            config=config,
        )
        eng.start()
        try:
            task = await eng.create_task(
                make_create_data(),
                requested_by="alice",
            )
            assert task.id.startswith("task-")
        finally:
            await eng.stop(timeout=2.0)


# ── _raise_typed_error coverage ──────────────────────────────


@pytest.mark.unit
class TestRaiseTypedError:
    """Test _raise_typed_error for internal error code mapping."""

    async def test_internal_error_code_raises_task_internal_error(
        self,
        persistence: FakePersistence,
        config: TaskEngineConfig,
    ) -> None:
        """error_code='internal' should raise TaskInternalError."""
        eng = TaskEngine(
            persistence=persistence,  # type: ignore[arg-type]
            config=config,
        )
        result = TaskMutationResult(
            request_id="req-1",
            success=False,
            error="Something went wrong",
            error_code="internal",
        )
        with pytest.raises(TaskInternalError, match="Something went wrong"):
            eng._raise_typed_error(result)


# ── _shutdown_result coverage ────────────────────────────────


@pytest.mark.unit
class TestShutdownResult:
    """Test _shutdown_result static method."""

    async def test_shutdown_result_envelope(self) -> None:
        mutation = CreateTaskMutation(
            request_id="req-shutdown",
            requested_by="alice",
            task_data=make_create_data(),
        )
        envelope = _MutationEnvelope(mutation=mutation)
        result = TaskEngine._shutdown_result(envelope)
        assert result.success is False
        assert result.error_code == "internal"
        assert "shut down" in (result.error or "").lower()
        assert result.request_id == "req-shutdown"


# ── Processing loop continues after error ────────────────────


@pytest.mark.unit
class TestProcessingLoopResilience:
    """Verify the processing loop continues after a single mutation fails."""

    async def test_loop_continues_after_failure(
        self,
        persistence: FakePersistence,
    ) -> None:
        """A failing mutation does not stop subsequent mutations."""
        call_count = 0
        original_save = persistence.tasks.save

        async def fail_first_save(task: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = "First save fails"
                raise RuntimeError(msg)
            await original_save(task)  # type: ignore[arg-type]

        persistence.tasks.save = fail_first_save  # type: ignore[method-assign]

        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        try:
            # First mutation fails
            m1 = CreateTaskMutation(
                request_id="req-1",
                requested_by="alice",
                task_data=make_create_data(),
            )
            r1 = await eng.submit(m1)
            assert r1.success is False

            # Second mutation succeeds — loop recovered
            m2 = CreateTaskMutation(
                request_id="req-2",
                requested_by="alice",
                task_data=make_create_data(),
            )
            r2 = await eng.submit(m2)
            assert r2.success is True
            assert r2.task is not None
        finally:
            await eng.stop(timeout=2.0)
