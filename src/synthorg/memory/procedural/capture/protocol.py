"""Protocol for procedural memory capture strategies.

Defines the interface for pluggable capture strategies that
determine when and how procedural memories are generated from
task execution outcomes.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.engine.loop_protocol import ExecutionResult
    from synthorg.engine.recovery import RecoveryResult
    from synthorg.memory.protocol import MemoryBackend


# capture/factory.py dispatch + HybridCaptureStrategy composition + 4 test
# files; pluggable procedural-memory capture seam.
@runtime_checkable
class CaptureStrategy(Protocol):
    """Strategy for capturing procedural memories from executions.

    Implementations determine which execution outcomes (failures,
    successes, or both) produce procedural memory entries.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    async def capture(
        self,
        *,
        execution_result: ExecutionResult,
        recovery_result: RecoveryResult | None,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        memory_backend: MemoryBackend,
    ) -> str | None:
        """Capture a procedural memory from the execution outcome.

        Args:
            execution_result: Result of the task execution.
            recovery_result: Recovery result (None if no recovery).
            agent_id: Agent that executed the task.
            task_id: Task that was executed.
            memory_backend: Memory backend for storage.

        Returns:
            Memory entry ID if a memory was captured, None otherwise.
        """
        ...
