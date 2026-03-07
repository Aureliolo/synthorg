"""Parallel agent execution orchestrator.

Coordinates multiple ``AgentEngine.run()`` calls in parallel using
structured concurrency (``asyncio.TaskGroup``), with error isolation,
concurrency limits, resource locking, and progress tracking.

Follows the ``ToolInvoker.invoke_all()`` pattern from
``tools/invoker.py`` — ``TaskGroup`` + optional ``Semaphore`` +
``_run_guarded()`` error isolation.
"""

import asyncio
import dataclasses
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ai_company.engine.errors import ParallelExecutionError, ResourceConflictError
from ai_company.engine.parallel_models import (
    AgentAssignment,
    AgentOutcome,
    ParallelExecutionGroup,
    ParallelExecutionResult,
    ParallelProgress,
)
from ai_company.engine.resource_lock import InMemoryResourceLock, ResourceLock
from ai_company.observability import get_logger

if TYPE_CHECKING:
    from ai_company.engine.agent_engine import AgentEngine
    from ai_company.engine.run_result import AgentRunResult
    from ai_company.engine.shutdown import ShutdownManager
from ai_company.observability.events.parallel import (
    PARALLEL_AGENT_COMPLETE,
    PARALLEL_AGENT_ERROR,
    PARALLEL_AGENT_START,
    PARALLEL_GROUP_COMPLETE,
    PARALLEL_GROUP_START,
    PARALLEL_PROGRESS_UPDATE,
    PARALLEL_VALIDATION_ERROR,
)

logger = get_logger(__name__)

ProgressCallback = Callable[[ParallelProgress], None]
"""Synchronous callback invoked on progress updates."""


@dataclasses.dataclass
class _ProgressState:
    """Mutable progress tracking — internal to ``execute_group()`` scope."""

    group_id: str
    total: int
    completed: int = 0
    in_progress: int = 0
    succeeded: int = 0
    failed: int = 0

    def snapshot(self) -> ParallelProgress:
        """Create a frozen progress snapshot."""
        return ParallelProgress(
            group_id=self.group_id,
            total=self.total,
            completed=self.completed,
            in_progress=self.in_progress,
            pending=self.total - self.completed - self.in_progress,
            succeeded=self.succeeded,
            failed=self.failed,
        )


class ParallelExecutor:
    """Orchestrates concurrent agent execution.

    Composition over inheritance — takes an ``AgentEngine`` and
    coordinates concurrent ``run()`` calls.

    Args:
        engine: Agent execution engine.
        shutdown_manager: Optional shutdown manager for task registration.
        resource_lock: Optional resource lock for exclusive file access.
            Defaults to ``InMemoryResourceLock`` if any assignments
            declare resource claims.
        progress_callback: Optional synchronous callback invoked on
            progress updates.
    """

    def __init__(
        self,
        *,
        engine: AgentEngine,
        shutdown_manager: ShutdownManager | None = None,
        resource_lock: ResourceLock | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._engine = engine
        self._shutdown_manager = shutdown_manager
        self._resource_lock = resource_lock
        self._progress_callback = progress_callback

    async def execute_group(
        self,
        group: ParallelExecutionGroup,
    ) -> ParallelExecutionResult:
        """Execute a parallel group of agent assignments.

        Args:
            group: The execution group to run.

        Returns:
            Result with all agent outcomes.

        Raises:
            ResourceConflictError: If resource claims conflict between
                assignments.
            ParallelExecutionError: If fatal errors (MemoryError,
                RecursionError) occurred during execution.
        """
        start = time.monotonic()

        logger.info(
            PARALLEL_GROUP_START,
            group_id=group.group_id,
            agent_count=len(group.assignments),
            max_concurrency=group.max_concurrency,
            fail_fast=group.fail_fast,
        )

        lock = self._resolve_lock(group)
        self._validate_resource_claims(group)

        if lock is not None:
            await self._acquire_all_locks(group, lock)

        semaphore = (
            asyncio.Semaphore(group.max_concurrency)
            if group.max_concurrency is not None
            else None
        )

        outcomes: dict[str, AgentOutcome] = {}
        fatal_errors: list[Exception] = []
        progress = _ProgressState(
            group_id=group.group_id,
            total=len(group.assignments),
        )

        try:
            async with asyncio.TaskGroup() as tg:
                for assignment in group.assignments:
                    tg.create_task(
                        self._run_guarded(
                            assignment=assignment,
                            group=group,
                            outcomes=outcomes,
                            fatal_errors=fatal_errors,
                            progress=progress,
                            semaphore=semaphore,
                            lock=lock,
                        ),
                    )
        except* Exception as eg:
            # TaskGroup wraps exceptions in ExceptionGroup when
            # fail_fast re-raises inside _run_guarded.
            # Outcomes from completed tasks are already collected.
            logger.debug(
                PARALLEL_GROUP_COMPLETE,
                group_id=group.group_id,
                note="TaskGroup exited with exceptions",
                exception_count=len(eg.exceptions),
            )

        if lock is not None:
            await self._release_all_locks(group, lock)

        duration = time.monotonic() - start

        result = ParallelExecutionResult(
            group_id=group.group_id,
            outcomes=tuple(
                outcomes.get(
                    a.task_id,
                    AgentOutcome(
                        task_id=a.task_id,
                        agent_id=a.agent_id,
                        error="Cancelled due to fail_fast",
                    ),
                )
                for a in group.assignments
            ),
            total_duration_seconds=duration,
        )

        logger.info(
            PARALLEL_GROUP_COMPLETE,
            group_id=group.group_id,
            succeeded=result.agents_succeeded,
            failed=result.agents_failed,
            duration_seconds=duration,
        )

        if fatal_errors:
            msg = (
                f"Parallel group {group.group_id!r} had "
                f"{len(fatal_errors)} fatal error(s)"
            )
            raise ParallelExecutionError(msg) from fatal_errors[0]

        return result

    async def _run_guarded(  # noqa: PLR0913
        self,
        *,
        assignment: AgentAssignment,
        group: ParallelExecutionGroup,
        outcomes: dict[str, AgentOutcome],
        fatal_errors: list[Exception],
        progress: _ProgressState,
        semaphore: asyncio.Semaphore | None,
        lock: ResourceLock | None,
    ) -> None:
        """Execute a single agent, isolating errors from siblings.

        Follows the ``ToolInvoker._run_guarded()`` pattern:
        - ``MemoryError``/``RecursionError`` → collected in fatal_errors
        - Regular ``Exception`` → stored as error outcome
        - ``BaseException`` → propagates through TaskGroup
        """
        task_id = assignment.task_id
        agent_id = assignment.agent_id

        if not self._register_with_shutdown(task_id, agent_id, outcomes):
            return

        try:
            progress.in_progress += 1
            self._emit_progress(progress)
            await self._execute_assignment(
                assignment,
                group,
                outcomes,
                progress,
                semaphore,
            )
        except (MemoryError, RecursionError) as exc:
            fatal_errors.append(exc)
            outcomes[task_id] = AgentOutcome(
                task_id=task_id,
                agent_id=agent_id,
                error=f"Fatal: {type(exc).__name__}: {exc}",
            )
            progress.failed += 1
        except Exception as exc:
            self._record_error_outcome(
                exc,
                assignment,
                group,
                outcomes,
                progress,
            )
            if group.fail_fast:
                raise
        finally:
            progress.in_progress = max(0, progress.in_progress - 1)
            progress.completed += 1
            self._emit_progress(progress)

            if lock is not None:
                for resource in assignment.resource_claims:
                    await lock.release(resource, agent_id)

            if self._shutdown_manager is not None:
                self._shutdown_manager.unregister_task(task_id)

    def _register_with_shutdown(
        self,
        task_id: str,
        agent_id: str,
        outcomes: dict[str, AgentOutcome],
    ) -> bool:
        """Register with shutdown manager. Returns False if shutdown."""
        if self._shutdown_manager is None:
            return True
        asyncio_task = asyncio.current_task()
        if asyncio_task is None:
            return True
        try:
            self._shutdown_manager.register_task(task_id, asyncio_task)
        except RuntimeError:
            outcomes[task_id] = AgentOutcome(
                task_id=task_id,
                agent_id=agent_id,
                error="Shutdown in progress",
            )
            return False
        return True

    async def _execute_assignment(
        self,
        assignment: AgentAssignment,
        group: ParallelExecutionGroup,
        outcomes: dict[str, AgentOutcome],
        progress: _ProgressState,
        semaphore: asyncio.Semaphore | None,
    ) -> None:
        """Run engine.run() with optional semaphore."""
        task_id = assignment.task_id
        agent_id = assignment.agent_id

        logger.info(
            PARALLEL_AGENT_START,
            group_id=group.group_id,
            agent_id=agent_id,
            task_id=task_id,
        )

        if semaphore is not None:
            await semaphore.acquire()
        try:
            run_result: AgentRunResult = await self._engine.run(
                identity=assignment.identity,
                task=assignment.task,
                completion_config=assignment.completion_config,
                max_turns=assignment.max_turns,
                memory_messages=assignment.memory_messages,
                timeout_seconds=assignment.timeout_seconds,
            )
            outcomes[task_id] = AgentOutcome(
                task_id=task_id,
                agent_id=agent_id,
                result=run_result,
            )
            progress.succeeded += 1
            logger.info(
                PARALLEL_AGENT_COMPLETE,
                group_id=group.group_id,
                agent_id=agent_id,
                task_id=task_id,
                success=True,
            )
        finally:
            if semaphore is not None:
                semaphore.release()

    def _record_error_outcome(
        self,
        exc: Exception,
        assignment: AgentAssignment,
        group: ParallelExecutionGroup,
        outcomes: dict[str, AgentOutcome],
        progress: _ProgressState,
    ) -> None:
        """Record a failed agent outcome."""
        error_msg = f"{type(exc).__name__}: {exc}"
        outcomes[assignment.task_id] = AgentOutcome(
            task_id=assignment.task_id,
            agent_id=assignment.agent_id,
            error=error_msg,
        )
        progress.failed += 1
        logger.warning(
            PARALLEL_AGENT_ERROR,
            group_id=group.group_id,
            agent_id=assignment.agent_id,
            task_id=assignment.task_id,
            error=error_msg,
        )

    def _resolve_lock(
        self,
        group: ParallelExecutionGroup,
    ) -> ResourceLock | None:
        """Return the resource lock to use, if any claims exist."""
        has_claims = any(a.resource_claims for a in group.assignments)
        if not has_claims:
            return self._resource_lock
        if self._resource_lock is not None:
            return self._resource_lock
        return InMemoryResourceLock()

    def _validate_resource_claims(
        self,
        group: ParallelExecutionGroup,
    ) -> None:
        """Check for overlapping resource claims between assignments.

        Raises:
            ResourceConflictError: If two assignments claim the same
                resource.
        """
        seen: dict[str, str] = {}
        for assignment in group.assignments:
            for resource in assignment.resource_claims:
                if resource in seen:
                    other = seen[resource]
                    msg = (
                        f"Resource conflict: {resource!r} claimed by "
                        f"both agent {other!r} and {assignment.agent_id!r}"
                    )
                    logger.warning(
                        PARALLEL_VALIDATION_ERROR,
                        group_id=group.group_id,
                        error=msg,
                    )
                    raise ResourceConflictError(msg)
                seen[resource] = assignment.agent_id

    async def _acquire_all_locks(
        self,
        group: ParallelExecutionGroup,
        lock: ResourceLock,
    ) -> None:
        """Acquire resource locks for all assignments."""
        for assignment in group.assignments:
            for resource in assignment.resource_claims:
                acquired = await lock.acquire(
                    resource,
                    assignment.agent_id,
                )
                if not acquired:
                    holder = lock.holder_of(resource)
                    msg = f"Failed to acquire lock on {resource!r}: held by {holder!r}"
                    logger.warning(
                        PARALLEL_VALIDATION_ERROR,
                        group_id=group.group_id,
                        error=msg,
                    )
                    # Release any locks already acquired for this group
                    await self._release_all_locks(group, lock)
                    raise ResourceConflictError(msg)

    async def _release_all_locks(
        self,
        group: ParallelExecutionGroup,
        lock: ResourceLock,
    ) -> None:
        """Release all resource locks for all assignments."""
        for assignment in group.assignments:
            await lock.release_all(assignment.agent_id)

    def _emit_progress(self, state: _ProgressState) -> None:
        """Emit a progress update via the callback, if configured."""
        if self._progress_callback is None:
            return
        snapshot = state.snapshot()
        logger.debug(
            PARALLEL_PROGRESS_UPDATE,
            group_id=snapshot.group_id,
            total=snapshot.total,
            completed=snapshot.completed,
            in_progress=snapshot.in_progress,
            pending=snapshot.pending,
        )
        try:
            self._progress_callback(snapshot)
        except Exception:
            logger.exception(
                PARALLEL_PROGRESS_UPDATE,
                error="Progress callback raised",
            )
