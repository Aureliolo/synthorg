"""Checkpoint recovery strategy.

Resumes execution from the last persisted checkpoint on crash.
After ``max_resume_attempts`` resume attempts, falls back to the
``FailAndReassignStrategy``.
"""

import asyncio
from typing import Final

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.checkpoint.models import (
    Checkpoint,  # noqa: TC001
    CheckpointConfig,  # noqa: TC001
)
from synthorg.engine.checkpoint.resume import cleanup_checkpoint_artifacts
from synthorg.engine.context import AgentContext  # noqa: TC001
from synthorg.engine.recovery import (
    FailAndReassignStrategy,
    RecoveryResult,
    RecoveryStrategy,
)
from synthorg.engine.task_execution import TaskExecution  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.checkpoint import (
    CHECKPOINT_LOAD_FAILED,
    CHECKPOINT_LOADED,
    CHECKPOINT_RECOVERY_FALLBACK,
    CHECKPOINT_RECOVERY_NO_CHECKPOINT,
    CHECKPOINT_RECOVERY_RESUME,
    CHECKPOINT_RECOVERY_START,
)
from synthorg.persistence.errors import PersistenceError
from synthorg.persistence.repositories import (
    CheckpointRepository,  # noqa: TC001
    HeartbeatRepository,  # noqa: TC001
)

logger = get_logger(__name__)

_MAX_TRACKED_EXECUTIONS: Final[int] = 10_000
"""Safety bound on the ``_resume_counts`` dict to prevent unbounded growth."""


class CheckpointRecoveryStrategy:
    """Resume from the last checkpoint on crash.

    Loads the latest checkpoint for the execution and returns a
    ``RecoveryResult`` with the serialized checkpoint context
    (making ``can_resume`` evaluate to ``True``).  After
    ``max_resume_attempts`` resume attempts, delegates to the
    fallback strategy (default: fail-and-reassign).

    Args:
        checkpoint_repo: Repository for loading checkpoints.
        heartbeat_repo: Repository for heartbeat cleanup on fallback.
        config: Checkpoint configuration (controls max_resume_attempts).
        fallback: Fallback recovery strategy; defaults to
            ``FailAndReassignStrategy``.
    """

    STRATEGY_TYPE: Final[str] = "checkpoint"

    def __init__(
        self,
        *,
        checkpoint_repo: CheckpointRepository,
        heartbeat_repo: HeartbeatRepository | None = None,
        config: CheckpointConfig,
        fallback: RecoveryStrategy | None = None,
    ) -> None:
        self._checkpoint_repo = checkpoint_repo
        self._heartbeat_repo = heartbeat_repo
        self._config = config
        self._fallback: RecoveryStrategy = fallback or FailAndReassignStrategy()
        # NOTE: _resume_counts is in-memory only.  Across process
        # restarts the counter resets, allowing up to max_resume_attempts
        # fresh attempts per lifetime.  Persisting the counter (e.g. in
        # the checkpoints table) is a planned improvement.
        self._resume_counts: dict[str, int] = {}
        self._resume_lock = asyncio.Lock()

    async def recover(
        self,
        *,
        task_execution: TaskExecution,
        error_message: str,
        context: AgentContext,
    ) -> RecoveryResult:
        """Apply checkpoint recovery.

        1. Load the latest checkpoint for the execution.
        2. If no checkpoint exists, delegate to fallback.
        3. If resume attempts are exhausted, delegate to fallback.
        4. Otherwise, return a ``RecoveryResult`` with the checkpoint
           context for resume.

        Args:
            task_execution: Current execution state.
            error_message: Description of the failure.
            context: Full agent context at the time of failure.

        Returns:
            ``RecoveryResult`` — either resumable or fallback.
        """
        execution_id = context.execution_id
        task_id = task_execution.task.id

        logger.info(
            CHECKPOINT_RECOVERY_START,
            execution_id=execution_id,
            task_id=task_id,
            strategy=self.STRATEGY_TYPE,
        )

        checkpoint = await self._load_latest_checkpoint(
            execution_id,
            task_id,
        )
        if checkpoint is None:
            return await self._delegate_to_fallback(
                task_execution=task_execution,
                error_message=error_message,
                context=context,
            )

        should_fallback, resume_attempt = await self._reserve_resume_attempt(
            execution_id,
            task_id,
        )
        if should_fallback:
            return await self._delegate_to_fallback(
                task_execution=task_execution,
                error_message=error_message,
                context=context,
            )

        return self._build_resume_result(
            task_execution=task_execution,
            error_message=error_message,
            context=context,
            checkpoint=checkpoint,
            resume_attempt=resume_attempt,
        )

    async def finalize(self, execution_id: str) -> None:
        """Clear resume counter after successful completion."""
        await self.clear_resume_count(execution_id)

    def get_strategy_type(self) -> str:
        """Return the strategy type identifier."""
        return self.STRATEGY_TYPE

    async def clear_resume_count(
        self,
        execution_id: NotBlankStr,
    ) -> None:
        """Clear the resume counter for a completed execution.

        Called after successful completion to reset the counter.
        Safe to call with unknown execution IDs (no-op).

        Args:
            execution_id: The execution identifier to clear.
        """
        async with self._resume_lock:
            self._resume_counts.pop(execution_id, None)

    # ── Private helpers ──────────────────────────────────────────

    async def _load_latest_checkpoint(
        self,
        execution_id: str,
        task_id: str,
    ) -> Checkpoint | None:
        """Load the latest checkpoint, returning ``None`` on failure.

        Only ``PersistenceError`` is swallowed (returns ``None`` to
        trigger fallback).  Other exceptions (e.g. ``ValueError``)
        propagate — they indicate programming errors, not transient
        storage failures.
        """
        try:
            checkpoint = await self._checkpoint_repo.get_latest(
                execution_id=execution_id,
            )
        except MemoryError, RecursionError:
            raise
        except PersistenceError:
            logger.exception(
                CHECKPOINT_LOAD_FAILED,
                execution_id=execution_id,
                task_id=task_id,
            )
            return None

        if checkpoint is None:
            logger.info(
                CHECKPOINT_RECOVERY_NO_CHECKPOINT,
                execution_id=execution_id,
                task_id=task_id,
            )
            return None

        logger.debug(
            CHECKPOINT_LOADED,
            execution_id=execution_id,
            checkpoint_id=checkpoint.id,
            turn_number=checkpoint.turn_number,
        )
        return checkpoint

    async def _reserve_resume_attempt(
        self,
        execution_id: str,
        task_id: str,
    ) -> tuple[bool, int]:
        """Reserve a resume attempt.

        Returns:
            A ``(should_fallback, resume_count)`` tuple.
            ``should_fallback`` is ``True`` when attempts are exhausted.
        """
        async with self._resume_lock:
            resume_count = self._resume_counts.get(execution_id, 0)
            if resume_count >= self._config.max_resume_attempts:
                logger.info(
                    CHECKPOINT_RECOVERY_FALLBACK,
                    execution_id=execution_id,
                    task_id=task_id,
                    resume_count=resume_count,
                    max_resume_attempts=self._config.max_resume_attempts,
                    reason="max_resume_attempts_exhausted",
                )
                self._resume_counts.pop(execution_id, None)
                return True, resume_count

            new_count = resume_count + 1
            self._resume_counts[execution_id] = new_count

            # Evict oldest entry (FIFO).  LRU would be more precise
            # but FIFO is acceptable given the 10k safety bound — the
            # dict only contains active executions that have crashed at
            # least once, which should be far fewer than 10k.
            if len(self._resume_counts) > _MAX_TRACKED_EXECUTIONS:
                oldest = next(iter(self._resume_counts))
                self._resume_counts.pop(oldest, None)

        return False, new_count

    def _build_resume_result(
        self,
        *,
        task_execution: TaskExecution,
        error_message: str,
        context: AgentContext,
        checkpoint: Checkpoint,
        resume_attempt: int,
    ) -> RecoveryResult:
        """Build a resumable ``RecoveryResult``."""
        execution_id = context.execution_id
        snapshot = context.to_snapshot()
        logger.info(
            CHECKPOINT_RECOVERY_RESUME,
            execution_id=execution_id,
            task_id=task_execution.task.id,
            checkpoint_id=checkpoint.id,
            turn_number=checkpoint.turn_number,
            resume_attempt=resume_attempt,
            max_resume_attempts=self._config.max_resume_attempts,
        )

        return RecoveryResult(
            task_execution=task_execution,
            strategy_type=self.STRATEGY_TYPE,
            context_snapshot=snapshot,
            error_message=error_message,
            checkpoint_context_json=checkpoint.context_json,
            resume_attempt=resume_attempt,
        )

    async def _delegate_to_fallback(
        self,
        *,
        task_execution: TaskExecution,
        error_message: str,
        context: AgentContext,
    ) -> RecoveryResult:
        """Delegate recovery to the fallback strategy.

        Also cleans up any orphaned checkpoint/heartbeat rows for
        this execution, since the resume path will not be entered.
        """
        await cleanup_checkpoint_artifacts(
            self._checkpoint_repo,
            self._heartbeat_repo,
            context.execution_id,
        )
        return await self._fallback.recover(
            task_execution=task_execution,
            error_message=error_message,
            context=context,
        )
