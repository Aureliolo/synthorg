"""Failure-only capture strategy for procedural memory.

Wraps the existing ``propose_procedural_memory`` pipeline to capture
procedural memories from task failures (when recovery_result is not None).
"""

import asyncio

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.loop_protocol import ExecutionResult  # noqa: TC001
from synthorg.engine.recovery import RecoveryResult  # noqa: TC001
from synthorg.memory.procedural.models import ProceduralMemoryConfig  # noqa: TC001
from synthorg.memory.procedural.pipeline import propose_procedural_memory
from synthorg.memory.procedural.proposer import ProceduralMemoryProposer  # noqa: TC001
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_CAPTURE_STORE_FAILED,
)

logger = get_logger(__name__)


class FailureCaptureStrategy:
    """Captures procedural memories from task failures only.

    Delegates to the existing ``propose_procedural_memory`` pipeline
    when a recovery result is present (indicating a failure).

    Args:
        proposer: ProceduralMemoryProposer instance.
        config: ProceduralMemoryConfig instance.
    """

    def __init__(
        self,
        *,
        proposer: ProceduralMemoryProposer,
        config: ProceduralMemoryConfig,
    ) -> None:
        self._proposer = proposer
        self._config = config

    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "failure"

    async def capture(
        self,
        *,
        execution_result: ExecutionResult,
        recovery_result: RecoveryResult | None,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        memory_backend: MemoryBackend,
    ) -> NotBlankStr | None:
        """Capture a procedural memory from a failed execution.

        Only captures when recovery_result is not None. Returns None
        if no recovery occurred (successful execution).

        Args:
            execution_result: Result of the task execution.
            recovery_result: Recovery result (None if no recovery).
            agent_id: Agent that executed the task.
            task_id: Task that was executed.
            memory_backend: Memory backend for storage.

        Returns:
            Memory entry ID if a memory was captured, None otherwise.
        """
        if recovery_result is None:
            return None

        try:
            return await propose_procedural_memory(
                execution_result,
                recovery_result,
                agent_id,
                task_id,
                proposer=self._proposer,
                memory_backend=memory_backend,
                config=self._config,
            )
        except MemoryError, RecursionError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                PROCEDURAL_CAPTURE_STORE_FAILED,
                agent_id=str(agent_id),
                task_id=str(task_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                strategy="failure",
            )
            return None
