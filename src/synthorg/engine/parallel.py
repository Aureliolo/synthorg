"""Parallel agent execution orchestrator.

Coordinates multiple ``AgentEngine.run()`` calls in parallel using
structured concurrency (``asyncio.TaskGroup``), with error isolation,
concurrency limits, resource locking, and progress tracking.

Inspired by the ``ToolInvoker.invoke_all()`` pattern from
``tools/invoker.py`` (``TaskGroup`` + ``Semaphore`` + guarded
execution), extended with fail-fast, progress tracking, and
``CancelledError`` handling.
"""

import asyncio
import dataclasses
import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import TYPE_CHECKING

from synthorg.engine.errors import ParallelExecutionError, ResourceConflictError
from synthorg.engine.parallel_models import (
    AgentAssignment,
    AgentOutcome,
    ParallelExecutionGroup,
    ParallelExecutionResult,
    ParallelProgress,
)
from synthorg.engine.resource_lock import InMemoryResourceLock, ResourceLock
from synthorg.observability import get_logger
from synthorg.observability.events.parallel import (
    PARALLEL_AGENT_CANCELLED,
    PARALLEL_AGENT_COMPLETE,
    PARALLEL_AGENT_ERROR,
    PARALLEL_AGENT_START,
    PARALLEL_GROUP_COMPLETE,
    PARALLEL_GROUP_START,
    PARALLEL_GROUP_SUPPRESSED,
    PARALLEL_LOCK_RELEASE_ERROR,
    PARALLEL_PROGRESS_UPDATE,
    PARALLEL_VALIDATION_ERROR,
)

if TYPE_CHECKING:
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.run_result import AgentRunResult
    from synthorg.engine.shutdown import ShutdownManager

logger = get_logger(__name__)

ProgressCallback = Callable[[ParallelProgress], None]
"""Synchronous callback invoked on progress updates.

Called directly (not awaited) from the executor's event loop;
must not block.  Async functions will produce un-awaited coroutines.
"""


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

        outcomes: dict[str, AgentOutcome] = {}
        fatal_errors: list[Exception] = []
        progress = _ProgressState(
            group_id=group.group_id,
            total=len(group.assignments),
        )

        task_error: Exception | None = None
        release_error: Exception | None = None
        try:
            if lock is not None:
                await self._acquire_all_locks(group, lock)
            await self._run_task_group(
                group,
                outcomes,
                fatal_errors,
                progress,
            )
        except Exception as exc:
            task_error = exc
        finally:
            if lock is not None:
                try:
                    await self._release_all_locks(group, lock)
                except Exception as exc:
                    logger.exception(
                        PARALLEL_LOCK_RELEASE_ERROR,
                        error="Failed to release resource locks",
                        group_id=group.group_id,
                    )
                    release_error = exc

        if release_error is not None:
            lock_msg = (
                f"Parallel group {group.group_id!r}: "
                "resource locks could not be released"
            )
            if task_error is not None:
                task_error.add_note(lock_msg)
            else:
                raise ParallelExecutionError(
                    lock_msg,
                ) from release_error

        if task_error is not None:
            raise task_error

        result = self._build_result(
            group,
            outcomes,
            time.monotonic() - start,
        )

        logger.info(
            PARALLEL_GROUP_COMPLETE,
            group_id=group.group_id,
            succeeded=result.agents_succeeded,
            failed=result.agents_failed,
            duration_seconds=result.total_duration_seconds,
        )

        if fatal_errors:
            msg = (
                f"Parallel group {group.group_id!r} had "
                f"{len(fatal_errors)} fatal error(s)"
            )
            logger.error(
                PARALLEL_AGENT_ERROR,
                group_id=group.group_id,
                fatal_error_count=len(fatal_errors),
                error=msg,
            )
            raise ParallelExecutionError(msg) from fatal_errors[0]

        return result

    async def _run_task_group(
        self,
        group: ParallelExecutionGroup,
        outcomes: dict[str, AgentOutcome],
        fatal_errors: list[Exception],
        progress: _ProgressState,
    ) -> None:
        """Run all assignments via TaskGroup."""
        semaphore = (
            asyncio.Semaphore(group.max_concurrency)
            if group.max_concurrency is not None
            else None
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
                        ),
                    )
        except* Exception as eg:
            # TaskGroup wraps exceptions in ExceptionGroup when
            # _run_guarded re-raises (fail_fast enabled).
            # Individual errors already logged in _record_error_outcome.
            logger.warning(
                PARALLEL_GROUP_SUPPRESSED,
                error=f"ExceptionGroup suppressed: {eg!r}",
                group_id=group.group_id,
                exception_count=len(eg.exceptions),
            )

    async def _run_guarded(  # noqa: PLR0913
        self,
        *,
        assignment: AgentAssignment,
        group: ParallelExecutionGroup,
        outcomes: dict[str, AgentOutcome],
        fatal_errors: list[Exception],
        progress: _ProgressState,
        semaphore: asyncio.Semaphore | None,
    ) -> None:
        """Execute a single agent, isolating errors from siblings.

        Follows the ``ToolInvoker._run_guarded()`` pattern:
        - ``MemoryError``/``RecursionError`` → collected in fatal_errors
        - Regular ``Exception`` → stored as error outcome;
          re-raised when ``fail_fast`` is enabled
        - ``CancelledError`` → stored as cancelled outcome, re-raised
        - ``BaseException`` → propagates through TaskGroup
        """
        task_id = assignment.task_id
        agent_id = assignment.agent_id

        if not self._register_with_shutdown(task_id, agent_id, outcomes):
            progress.completed += 1
            progress.failed += 1
            self._emit_progress(progress)
            return

        try:
            await self._execute_assignment(
                assignment=assignment,
                group_id=group.group_id,
                outcomes=outcomes,
                progress=progress,
                semaphore=semaphore,
            )
        except (MemoryError, RecursionError) as exc:
            self._record_fatal_outcome(
                exc,
                assignment,
                group,
                outcomes,
                fatal_errors,
                progress,
            )
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
        except asyncio.CancelledError:
            outcomes[task_id] = AgentOutcome(
                task_id=task_id,
                agent_id=agent_id,
                error="Cancelled",
            )
            progress.failed += 1
            logger.warning(
                PARALLEL_AGENT_CANCELLED,
                agent_id=agent_id,
                task_id=task_id,
                group_id=group.group_id,
            )
            raise
        finally:
            progress.completed += 1

            if self._shutdown_manager is not None:
                self._shutdown_manager.unregister_task(task_id)

            self._emit_progress(progress)

    def _register_with_shutdown(
        self,
        task_id: str,
        agent_id: str,
        outcomes: dict[str, AgentOutcome],
    ) -> bool:
        """Register with shutdown manager.

        Returns ``False`` and records an error outcome if shutdown
        is already in progress.
        """
        if self._shutdown_manager is None:
            return True
        asyncio_task = asyncio.current_task()
        if asyncio_task is None:
            return True
        try:
            self._shutdown_manager.register_task(task_id, asyncio_task)
        except RuntimeError as exc:
            logger.warning(
                PARALLEL_AGENT_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error=f"Failed to register with shutdown manager: {exc}",
            )
            outcomes[task_id] = AgentOutcome(
                task_id=task_id,
                agent_id=agent_id,
                error="Shutdown in progress",
            )
            return False
        return True

    async def _execute_assignment(
        self,
        *,
        assignment: AgentAssignment,
        group_id: str,
        outcomes: dict[str, AgentOutcome],
        progress: _ProgressState,
        semaphore: asyncio.Semaphore | None,
    ) -> None:
        """Run ``engine.run()`` under optional semaphore and record outcome."""
        task_id = assignment.task_id
        agent_id = assignment.agent_id

        logger.info(
            PARALLEL_AGENT_START,
            group_id=group_id,
            agent_id=agent_id,
            task_id=task_id,
        )

        ctx = semaphore if semaphore is not None else nullcontext()
        async with ctx:
            progress.in_progress += 1
            self._emit_progress(progress)
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
                success = run_result.is_success
                if success:
                    progress.succeeded += 1
                else:
                    progress.failed += 1
                logger.info(
                    PARALLEL_AGENT_COMPLETE,
                    group_id=group_id,
                    agent_id=agent_id,
                    task_id=task_id,
                    success=success,
                )
            finally:
                progress.in_progress -= 1

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

    def _record_fatal_outcome(  # noqa: PLR0913
        self,
        exc: MemoryError | RecursionError,
        assignment: AgentAssignment,
        group: ParallelExecutionGroup,
        outcomes: dict[str, AgentOutcome],
        fatal_errors: list[Exception],
        progress: _ProgressState,
    ) -> None:
        """Record a fatal error outcome (MemoryError/RecursionError)."""
        error_msg = f"Fatal: {type(exc).__name__}: {exc}"
        logger.exception(
            PARALLEL_AGENT_ERROR,
            group_id=group.group_id,
            agent_id=assignment.agent_id,
            task_id=assignment.task_id,
            error=error_msg,
        )
        fatal_errors.append(exc)
        outcomes[assignment.task_id] = AgentOutcome(
            task_id=assignment.task_id,
            agent_id=assignment.agent_id,
            error=error_msg,
        )
        progress.failed += 1

    def _build_result(
        self,
        group: ParallelExecutionGroup,
        outcomes: dict[str, AgentOutcome],
        duration: float,
    ) -> ParallelExecutionResult:
        """Build execution result, filling cancelled outcomes."""
        return ParallelExecutionResult(
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

    def _resolve_lock(
        self,
        group: ParallelExecutionGroup,
    ) -> ResourceLock | None:
        """Return the resource lock to use, or ``None`` if not needed.

        When no assignments declare resource claims, returns ``None``
        (no locking needed).  When claims exist, falls back to
        a shared ``InMemoryResourceLock()`` if no lock was injected.
        """
        has_claims = any(a.resource_claims for a in group.assignments)
        if not has_claims:
            return None
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
            holder_id = f"{group.group_id}:{assignment.task_id}"
            for resource in assignment.resource_claims:
                acquired = await lock.acquire(
                    resource,
                    holder_id,
                )
                if not acquired:
                    current_holder = lock.holder_of(resource)
                    msg = (
                        f"Failed to acquire lock on {resource!r}: "
                        f"held by {current_holder!r}"
                    )
                    logger.warning(
                        PARALLEL_VALIDATION_ERROR,
                        group_id=group.group_id,
                        error=msg,
                    )
                    await self._release_all_locks(group, lock)
                    raise ResourceConflictError(msg)

    async def _release_all_locks(
        self,
        group: ParallelExecutionGroup,
        lock: ResourceLock,
    ) -> None:
        """Release all resource locks for all assignments."""
        for assignment in group.assignments:
            holder_id = f"{group.group_id}:{assignment.task_id}"
            await lock.release_all(holder_id)

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
                group_id=snapshot.group_id,
            )
