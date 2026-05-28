"""Hybrid capture strategy that combines failure and success capture.

Routes to failure or success strategy based on execution outcome
(presence of recovery_result and termination reason).
"""

from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.recovery import RecoveryResult
from synthorg.memory.procedural.capture.protocol import CaptureStrategy
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger

logger = get_logger(__name__)


class HybridCaptureStrategy:
    """Combines failure and success capture strategies.

    Routes to the appropriate strategy based on execution outcome:
    - If recovery_result is not None: delegates to failure_strategy
    - If execution succeeded (no recovery): delegates to success_strategy

    Args:
        failure_strategy: Strategy for capturing from failures.
        success_strategy: Strategy for capturing from successes.
    """

    def __init__(
        self,
        *,
        failure_strategy: CaptureStrategy,
        success_strategy: CaptureStrategy,
    ) -> None:
        self._failure_strategy = failure_strategy
        self._success_strategy = success_strategy

    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "hybrid"

    async def capture(
        self,
        *,
        execution_result: ExecutionResult,
        recovery_result: RecoveryResult | None,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        memory_backend: MemoryBackend,
    ) -> NotBlankStr | None:
        """Route to appropriate strategy based on execution outcome.

        Args:
            execution_result: Result of the task execution.
            recovery_result: Recovery result (None if no recovery).
            agent_id: Agent that executed the task.
            task_id: Task that was executed.
            memory_backend: Memory backend for storage.

        Returns:
            Memory entry ID if a memory was captured, None otherwise.
        """
        if recovery_result is not None:
            return await self._failure_strategy.capture(
                execution_result=execution_result,
                recovery_result=recovery_result,
                agent_id=agent_id,
                task_id=task_id,
                memory_backend=memory_backend,
            )

        return await self._success_strategy.capture(
            execution_result=execution_result,
            recovery_result=recovery_result,
            agent_id=agent_id,
            task_id=task_id,
            memory_backend=memory_backend,
        )
