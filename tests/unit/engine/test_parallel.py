"""Tests for the ParallelExecutor."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.enums import (
    Complexity,
    Priority,
    SeniorityLevel,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ParallelExecutionError, ResourceConflictError
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.parallel_models import (
    AgentAssignment,
    ParallelExecutionGroup,
    ParallelProgress,
)
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.resource_lock import InMemoryResourceLock
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.shutdown import ShutdownManager
from synthorg.observability.events.parallel import PARALLEL_AGENT_CANCELLED


def _make_identity(
    name: str = "test-agent",
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="engineer",
        department="engineering",
        level=SeniorityLevel.MID,
        hiring_date=date(2026, 1, 15),
        personality=PersonalityConfig(traits=("analytical",)),
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
    )


def _make_task(title: str = "test-task") -> Task:
    return Task(
        id=f"task-{title}",
        title=title,
        description="A test task",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="tester",
        assigned_to="test-agent",
        status=TaskStatus.ASSIGNED,
        estimated_complexity=Complexity.SIMPLE,
    )


def _make_run_result(
    identity: AgentIdentity,
    task: Task,
    reason: TerminationReason = TerminationReason.COMPLETED,
) -> AgentRunResult:
    ctx = AgentContext.from_identity(identity, task=task)
    error_msg = "test error" if reason == TerminationReason.ERROR else None
    execution_result = ExecutionResult(
        context=ctx,
        termination_reason=reason,
        error_message=error_msg,
    )
    return AgentRunResult(
        execution_result=execution_result,
        system_prompt=SystemPrompt(
            content="test",
            template_version="1.0",
            estimated_tokens=1,
            sections=("identity",),
            metadata={"agent_id": str(identity.id)},
        ),
        duration_seconds=0.5,
        agent_id=str(identity.id),
        task_id=task.id,
    )


def _make_assignment(
    name: str = "agent",
    title: str = "task",
    **kwargs: object,
) -> AgentAssignment:
    return AgentAssignment(
        identity=_make_identity(name),
        task=_make_task(title),
        **kwargs,  # type: ignore[arg-type]
    )


def _make_group(
    *assignments: AgentAssignment,
    group_id: str = "test-group",
    **kwargs: object,
) -> ParallelExecutionGroup:
    if not assignments:
        assignments = (_make_assignment(),)
    return ParallelExecutionGroup(
        group_id=group_id,
        assignments=assignments,
        **kwargs,  # type: ignore[arg-type]
    )


def _mock_engine(
    side_effect: object = None,
) -> MagicMock:
    """Create a mock AgentEngine with an async run method."""
    engine = MagicMock()
    engine.run = AsyncMock(side_effect=side_effect)
    return engine


@pytest.mark.unit
class TestParallelExecutorConstruction:
    """ParallelExecutor construction."""

    def test_minimal(self) -> None:
        engine = _mock_engine()
        executor = ParallelExecutor(engine=engine)
        assert executor is not None

    def test_with_all_options(self) -> None:
        engine = _mock_engine()
        sm = ShutdownManager()
        lock = InMemoryResourceLock()
        cb = MagicMock()
        executor = ParallelExecutor(
            engine=engine,
            shutdown_manager=sm,
            resource_lock=lock,
            progress_callback=cb,
        )
        assert executor is not None


@pytest.mark.unit
class TestParallelExecutorSingleAgent:
    """Single-agent parallel execution (degenerate case)."""

    async def test_single_success(self) -> None:
        a = _make_assignment("a1", "t1")
        run_result = _make_run_result(a.identity, a.task)
        engine = _mock_engine(side_effect=[run_result])
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a)

        result = await executor.execute_group(group)

        assert result.group_id == "test-group"
        assert len(result.outcomes) == 1
        assert result.all_succeeded is True
        assert result.total_duration_seconds > 0
        engine.run.assert_awaited_once()

    async def test_single_failure(self) -> None:
        a = _make_assignment("a1", "t1")
        engine = _mock_engine(
            side_effect=RuntimeError("agent crashed"),
        )
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a)

        result = await executor.execute_group(group)

        assert len(result.outcomes) == 1
        assert result.all_succeeded is False
        assert result.outcomes[0].error is not None
        assert "agent crashed" in result.outcomes[0].error


@pytest.mark.unit
class TestParallelExecutorMultipleAgents:
    """Multiple agents running in parallel."""

    async def test_two_agents_both_succeed(self) -> None:
        a1 = _make_assignment("a1", "t1")
        a2 = _make_assignment("a2", "t2")
        r1 = _make_run_result(a1.identity, a1.task)
        r2 = _make_run_result(a2.identity, a2.task)
        engine = _mock_engine(side_effect=[r1, r2])
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a1, a2)

        result = await executor.execute_group(group)

        assert result.agents_succeeded == 2
        assert result.agents_failed == 0
        assert result.all_succeeded is True
        assert engine.run.await_count == 2
        # Verify outcome pairing
        outcome_pairs = sorted((o.agent_id, o.task_id) for o in result.outcomes)
        expected_pairs = sorted((str(a.identity.id), a.task.id) for a in (a1, a2))
        assert outcome_pairs == expected_pairs

    async def test_one_fails_one_succeeds(self) -> None:
        a1 = _make_assignment("a1", "t1")
        a2 = _make_assignment("a2", "t2")
        r1 = _make_run_result(a1.identity, a1.task)

        call_count = 0

        async def side_effect(**kwargs: object) -> AgentRunResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return r1
            msg = "agent 2 crashed"
            raise RuntimeError(msg)

        engine = _mock_engine()
        engine.run = AsyncMock(side_effect=side_effect)
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a1, a2)

        result = await executor.execute_group(group)

        assert result.agents_succeeded + result.agents_failed == 2
        # At least one succeeded, at least one failed
        assert result.agents_failed >= 1
        assert result.all_succeeded is False


@pytest.mark.unit
class TestParallelExecutorConcurrencyLimit:
    """max_concurrency semaphore behavior."""

    async def test_concurrency_limited(self) -> None:
        """Verify semaphore limits concurrent executions."""
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def track_concurrency(**kwargs: object) -> AgentRunResult:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.05)
            async with lock:
                current_concurrent -= 1
            identity = kwargs.get("identity")
            task = kwargs.get("task")
            return _make_run_result(identity, task)  # type: ignore[arg-type]

        assignments = [_make_assignment(f"a{i}", f"t{i}") for i in range(4)]
        engine = _mock_engine()
        engine.run = AsyncMock(side_effect=track_concurrency)
        executor = ParallelExecutor(engine=engine)
        group = _make_group(
            *assignments,
            max_concurrency=2,
        )

        result = await executor.execute_group(group)

        assert result.all_succeeded is True
        assert max_concurrent <= 2


@pytest.mark.unit
class TestParallelExecutorFailFast:
    """fail_fast cancellation behavior."""

    async def test_fail_fast_cancels_siblings(self) -> None:
        a1 = _make_assignment("a1", "t1")
        a2 = _make_assignment("a2", "t2")
        a3 = _make_assignment("a3", "t3")

        call_count = 0

        async def side_effect(**kwargs: object) -> AgentRunResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = "first agent failed"
                raise RuntimeError(msg)
            # Others take longer (would be cancelled)
            await asyncio.sleep(10)
            identity = kwargs.get("identity")
            task = kwargs.get("task")
            return _make_run_result(identity, task)  # type: ignore[arg-type]

        engine = _mock_engine()
        engine.run = AsyncMock(side_effect=side_effect)
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a1, a2, a3, fail_fast=True)

        result = await executor.execute_group(group)

        assert result.agents_failed >= 1
        # Some outcomes should be cancellation errors
        cancel_outcomes = [
            o for o in result.outcomes if o.error and "cancel" in o.error.lower()
        ]
        assert len(cancel_outcomes) >= 1


@pytest.mark.unit
class TestParallelExecutorResourceLocking:
    """Resource claim and lock behavior."""

    async def test_non_conflicting_claims(self) -> None:
        a1 = _make_assignment(
            "a1",
            "t1",
            resource_claims=("src/a.py",),
        )
        a2 = _make_assignment(
            "a2",
            "t2",
            resource_claims=("src/b.py",),
        )
        r1 = _make_run_result(a1.identity, a1.task)
        r2 = _make_run_result(a2.identity, a2.task)
        engine = _mock_engine(side_effect=[r1, r2])
        lock = InMemoryResourceLock()
        executor = ParallelExecutor(
            engine=engine,
            resource_lock=lock,
        )
        group = _make_group(a1, a2)

        result = await executor.execute_group(group)

        assert result.all_succeeded is True

    async def test_conflicting_claims_raises(self) -> None:
        a1 = _make_assignment(
            "a1",
            "t1",
            resource_claims=("src/shared.py",),
        )
        a2 = _make_assignment(
            "a2",
            "t2",
            resource_claims=("src/shared.py",),
        )
        engine = _mock_engine()
        lock = InMemoryResourceLock()
        executor = ParallelExecutor(
            engine=engine,
            resource_lock=lock,
        )
        group = _make_group(a1, a2)

        with pytest.raises(ResourceConflictError):
            await executor.execute_group(group)

    async def test_locks_released_after_execution(self) -> None:
        a1 = _make_assignment(
            "a1",
            "t1",
            resource_claims=("src/a.py",),
        )
        r1 = _make_run_result(a1.identity, a1.task)
        engine = _mock_engine(side_effect=[r1])
        lock = InMemoryResourceLock()
        executor = ParallelExecutor(
            engine=engine,
            resource_lock=lock,
        )
        group = _make_group(a1)

        await executor.execute_group(group)

        assert not lock.is_locked("src/a.py")

    async def test_external_lock_holder_raises(self) -> None:
        a1 = _make_assignment(
            "a1",
            "t1",
            resource_claims=("src/shared.py",),
        )
        engine = _mock_engine()
        lock = InMemoryResourceLock()
        await lock.acquire("src/shared.py", "external-agent")
        executor = ParallelExecutor(
            engine=engine,
            resource_lock=lock,
        )
        group = _make_group(a1)

        with pytest.raises(ResourceConflictError):
            await executor.execute_group(group)

        assert lock.holder_of("src/shared.py") == "external-agent"

    async def test_auto_creates_lock_for_claims(self) -> None:
        a1 = _make_assignment(
            "a1",
            "t1",
            resource_claims=("src/a.py",),
        )
        a2 = _make_assignment(
            "a2",
            "t2",
            resource_claims=("src/b.py",),
        )
        r1 = _make_run_result(a1.identity, a1.task)
        r2 = _make_run_result(a2.identity, a2.task)
        engine = _mock_engine(side_effect=[r1, r2])
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a1, a2)

        result = await executor.execute_group(group)

        assert result.all_succeeded is True

    async def test_auto_created_lock_detects_conflicts(self) -> None:
        a1 = _make_assignment(
            "a1",
            "t1",
            resource_claims=("src/shared.py",),
        )
        a2 = _make_assignment(
            "a2",
            "t2",
            resource_claims=("src/shared.py",),
        )
        engine = _mock_engine()
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a1, a2)

        with pytest.raises(ResourceConflictError):
            await executor.execute_group(group)

    async def test_locks_released_on_error(self) -> None:
        a1 = _make_assignment(
            "a1",
            "t1",
            resource_claims=("src/a.py",),
        )
        engine = _mock_engine(
            side_effect=RuntimeError("crash"),
        )
        lock = InMemoryResourceLock()
        executor = ParallelExecutor(
            engine=engine,
            resource_lock=lock,
        )
        group = _make_group(a1)

        await executor.execute_group(group)

        assert not lock.is_locked("src/a.py")


@pytest.mark.unit
class TestParallelExecutorProgress:
    """Progress callback invocation."""

    async def test_progress_callback_called(self) -> None:
        a1 = _make_assignment("a1", "t1")
        r1 = _make_run_result(a1.identity, a1.task)
        engine = _mock_engine(side_effect=[r1])
        progress_updates: list[ParallelProgress] = []

        def on_progress(p: ParallelProgress) -> None:
            progress_updates.append(p)

        executor = ParallelExecutor(
            engine=engine,
            progress_callback=on_progress,
        )
        group = _make_group(a1)

        await executor.execute_group(group)

        assert len(progress_updates) >= 1
        # Final update should show completion
        final = progress_updates[-1]
        assert final.completed == 1
        assert final.total == 1

    async def test_progress_callback_exception_swallowed(self) -> None:
        a1 = _make_assignment("a1", "t1")
        r1 = _make_run_result(a1.identity, a1.task)
        engine = _mock_engine(side_effect=[r1])

        def bad_callback(p: ParallelProgress) -> None:
            msg = "callback error"
            raise ValueError(msg)

        executor = ParallelExecutor(
            engine=engine,
            progress_callback=bad_callback,
        )
        group = _make_group(a1)

        result = await executor.execute_group(group)

        assert result.all_succeeded is True

    async def test_progress_tracks_multiple_agents(self) -> None:
        a1 = _make_assignment("a1", "t1")
        a2 = _make_assignment("a2", "t2")
        r1 = _make_run_result(a1.identity, a1.task)
        r2 = _make_run_result(a2.identity, a2.task)
        engine = _mock_engine(side_effect=[r1, r2])
        progress_updates: list[ParallelProgress] = []

        def on_progress(p: ParallelProgress) -> None:
            progress_updates.append(p)

        executor = ParallelExecutor(
            engine=engine,
            progress_callback=on_progress,
        )
        group = _make_group(a1, a2)

        await executor.execute_group(group)

        assert len(progress_updates) >= 2
        final = progress_updates[-1]
        assert final.completed == 2
        assert final.total == 2


@pytest.mark.unit
class TestParallelExecutorShutdown:
    """Shutdown manager integration."""

    async def test_shutdown_in_progress_rejected(self) -> None:
        a1 = _make_assignment("a1", "t1")
        engine = _mock_engine()
        sm = ShutdownManager()
        sm.register_task = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("Shutdown in progress"),
        )
        progress_updates: list[ParallelProgress] = []
        executor = ParallelExecutor(
            engine=engine,
            shutdown_manager=sm,
            progress_callback=progress_updates.append,
        )
        group = _make_group(a1)

        result = await executor.execute_group(group)

        assert result.all_succeeded is False
        assert result.outcomes[0].error == "Shutdown in progress"
        engine.run.assert_not_awaited()
        # Progress must be tracked even for rejected tasks
        assert len(progress_updates) >= 1
        final = progress_updates[-1]
        assert final.completed == 1
        assert final.failed == 1
        assert final.pending == 0

    async def test_shutdown_manager_integration(self) -> None:
        a1 = _make_assignment("a1", "t1")
        r1 = _make_run_result(a1.identity, a1.task)
        engine = _mock_engine(side_effect=[r1])
        sm = ShutdownManager()
        executor = ParallelExecutor(
            engine=engine,
            shutdown_manager=sm,
        )
        group = _make_group(a1)

        result = await executor.execute_group(group)

        assert result.all_succeeded is True


@pytest.mark.unit
class TestParallelExecutorFatalErrors:
    """Fatal error (MemoryError/RecursionError) handling."""

    async def test_memory_error_propagates(self) -> None:
        a1 = _make_assignment("a1", "t1")
        engine = _mock_engine(side_effect=MemoryError("OOM"))
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a1)

        with pytest.raises(ParallelExecutionError, match="fatal"):
            await executor.execute_group(group)

    async def test_recursion_error_propagates(self) -> None:
        a1 = _make_assignment("a1", "t1")
        engine = _mock_engine(side_effect=RecursionError("stack"))
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a1)

        with pytest.raises(ParallelExecutionError, match="fatal"):
            await executor.execute_group(group)


@pytest.mark.unit
class TestParallelExecutorCancellation:
    """CancelledError logging during fail_fast cancellation."""

    async def test_cancelled_agent_is_logged(self) -> None:
        """CancelledError emits PARALLEL_AGENT_CANCELLED event."""
        import structlog

        a1 = _make_assignment("a1", "t1")
        a2 = _make_assignment("a2", "t2")

        call_count = 0
        peer_started = asyncio.Event()

        async def side_effect(**kwargs: object) -> AgentRunResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await peer_started.wait()
                msg = "fail fast"
                raise RuntimeError(msg)
            peer_started.set()
            await asyncio.sleep(10)
            identity = kwargs.get("identity")
            task = kwargs.get("task")
            return _make_run_result(identity, task)  # type: ignore[arg-type]

        engine = _mock_engine()
        engine.run = AsyncMock(side_effect=side_effect)
        executor = ParallelExecutor(engine=engine)
        group = _make_group(a1, a2, fail_fast=True)

        with structlog.testing.capture_logs() as cap:
            await executor.execute_group(group)

        cancelled_events = [
            e for e in cap if e.get("event") == PARALLEL_AGENT_CANCELLED
        ]
        assert len(cancelled_events) >= 1
        for ev in cancelled_events:
            assert ev["group_id"] == group.group_id


@pytest.mark.unit
class TestParallelExecutorInProgressSemantics:
    """Progress callback in_progress count respects concurrency limit."""

    async def test_in_progress_respects_concurrency_limit(self) -> None:
        """in_progress never exceeds max_concurrency in progress callbacks."""
        max_in_progress = 0

        async def track_concurrency(**kwargs: object) -> AgentRunResult:
            await asyncio.sleep(0.05)
            identity = kwargs.get("identity")
            task = kwargs.get("task")
            return _make_run_result(identity, task)  # type: ignore[arg-type]

        assignments = [_make_assignment(f"a{i}", f"t{i}") for i in range(4)]
        engine = _mock_engine()
        engine.run = AsyncMock(side_effect=track_concurrency)
        progress_updates: list[ParallelProgress] = []

        def on_progress(p: ParallelProgress) -> None:
            nonlocal max_in_progress
            progress_updates.append(p)
            max_in_progress = max(max_in_progress, p.in_progress)

        executor = ParallelExecutor(
            engine=engine,
            progress_callback=on_progress,
        )
        group = _make_group(
            *assignments,
            max_concurrency=2,
        )

        result = await executor.execute_group(group)

        assert result.all_succeeded is True
        assert progress_updates
        assert any(p.in_progress > 0 for p in progress_updates)
        assert max_in_progress <= 2
