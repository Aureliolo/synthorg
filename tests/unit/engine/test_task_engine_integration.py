"""Integration tests for TaskEngine: publishing, ordering, queue, versioning, drain."""

import asyncio
import contextlib

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.engine.errors import TaskEngineQueueFullError
from synthorg.engine.task_engine import TaskEngine, _MutationEnvelope
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.task_engine_models import (
    CreateTaskMutation,
    DeleteTaskMutation,
    TransitionTaskMutation,
    UpdateTaskMutation,
)
from tests.unit.engine.task_engine_helpers import (
    FailingMessageBus,
    FakeMessageBus,
    FakePersistence,
    make_create_data,
)

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
            make_create_data(),
            requested_by="alice",
        )
        # Yield to event loop so the processing loop completes snapshot publication
        await asyncio.sleep(0)
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
                make_create_data(),
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
                make_create_data(),
                requested_by="alice",
            )
            await asyncio.sleep(0)
            assert len(message_bus.published) == 0
        finally:
            await eng.stop(timeout=2.0)

    async def test_pending_mutations_drained_on_stop(
        self,
        persistence: FakePersistence,
    ) -> None:
        """Tasks submitted before stop() are processed during drain."""
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()

        # Submit concurrently using structured concurrency
        async with asyncio.TaskGroup() as tg:
            results = [
                tg.create_task(
                    eng.create_task(make_create_data(), requested_by="alice"),
                )
                for _ in range(5)
            ]

        # Yield to let processing complete before stopping
        await asyncio.sleep(0)

        # Stop — drain remaining if any
        await eng.stop(timeout=5.0)

        # All futures resolved
        assert len(results) == 5
        stored = await persistence.tasks.list_tasks()
        assert len(stored) == 5


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
                    make_create_data(title=f"Task {i}"),
                    requested_by="alice",
                )
                for i in range(10)
            ),
        )
        assert len(tasks) == 10
        ids = {t.id for t in tasks}
        assert len(ids) == 10  # all unique


# ── Queue backpressure ────────────────────────────────────────


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
        # Directly manipulate internal state because triggering a full-queue
        # condition through the public API is difficult: we need to fill the
        # queue without the background loop draining it, so we set _running
        # without calling start() (no processing task) and enqueue manually.
        eng._running = True

        # First submit fills the queue
        mutation1 = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(),
        )
        eng._queue.put_nowait(_MutationEnvelope(mutation=mutation1))

        # Second submit should fail because queue is full
        mutation2 = CreateTaskMutation(
            request_id="req-2",
            requested_by="alice",
            task_data=make_create_data(),
        )
        with pytest.raises(TaskEngineQueueFullError, match="queue is full"):
            await eng.submit(mutation2)

        eng._running = False


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
            task_data=make_create_data(),
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
            make_create_data(),
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
        assert result.error_code == "version_conflict"
        assert "conflict" in (result.error or "").lower()

    async def test_version_reset_on_delete(
        self,
        engine: TaskEngine,
    ) -> None:
        task = await engine.create_task(
            make_create_data(),
            requested_by="alice",
        )
        delete = DeleteTaskMutation(
            request_id="req-3",
            requested_by="alice",
            task_id=task.id,
        )
        result = await engine.submit(delete)
        assert result.version == 0

    async def test_transition_version_conflict(
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
            expected_version=99,
        )
        result = await engine.submit(mutation)
        assert result.success is False
        assert result.error_code == "version_conflict"
        assert "conflict" in (result.error or "").lower()


# ── Drain timeout ─────────────────────────────────────────────


@pytest.mark.unit
class TestDrainTimeout:
    """Verify drain-timeout cleanup resolves outstanding futures."""

    async def test_drain_timeout_resolves_pending_futures(
        self,
        persistence: FakePersistence,
    ) -> None:
        """Futures still in queue are failed when stop() times out."""
        # Block the processing loop with a slow save
        block = asyncio.Event()
        entered_save = asyncio.Event()
        original_save = persistence.tasks.save

        async def slow_save(task: object) -> None:
            entered_save.set()
            await block.wait()
            await original_save(task)  # type: ignore[arg-type]

        persistence.tasks.save = slow_save  # type: ignore[method-assign]

        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()

        # Submit a task — it'll block in slow_save, holding the processing loop
        blocked_task = asyncio.create_task(
            eng.create_task(make_create_data(), requested_by="alice")
        )

        # Queue a second task directly so it's definitely waiting
        mutation2 = CreateTaskMutation(
            request_id="req-queued",
            requested_by="alice",
            task_data=make_create_data(),
        )
        envelope = _MutationEnvelope(mutation=mutation2)
        # Wait until slow_save is entered before queuing the second task
        await entered_save.wait()
        eng._queue.put_nowait(envelope)

        # Stop with a very short timeout — loop is blocked, so timeout fires
        await eng.stop(timeout=0.05)

        # The queued envelope (not yet processed) must be failed
        assert envelope.future.done()
        result = envelope.future.result()
        assert result.success is False
        assert result.error_code == "internal"

        # Release the block so slow_save can finish, then cancel the blocked task
        # (its future was never set because the processing loop was cancelled)
        block.set()
        blocked_task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await blocked_task
