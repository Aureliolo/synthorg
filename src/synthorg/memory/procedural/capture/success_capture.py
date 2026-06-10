"""Success-only capture strategy for procedural memory.

Captures procedural memories from successful task executions based on
a configurable quality threshold derived from the proposer's confidence.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.recovery import RecoveryResult
from synthorg.memory.filter import NON_INFERABLE_TAG
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.procedural.success_proposer import (
    SuccessMemoryProposer,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_CAPTURE_QUALITY_BELOW_THRESHOLD,
    PROCEDURAL_CAPTURE_STORE_FAILED,
    PROCEDURAL_CAPTURE_STORED,
)

logger = get_logger(__name__)
_DEFAULT_MIN_QUALITY_SCORE: Final[float] = 8.0


class SuccessCaptureStrategy:
    """Captures procedural memories from successful task executions.

    Only fires when execution succeeds (termination_reason is SUCCESS)
    and the proposed memory meets the quality threshold.

    Args:
        proposer: SuccessMemoryProposer instance.
        config: ProceduralMemoryConfig instance.
        min_quality_score: Minimum quality score (0-10) required to store.
            Derived from confidence: quality = confidence * 10.
            Default 8.0 means confidence >= 0.8.
    """

    def __init__(
        self,
        *,
        proposer: SuccessMemoryProposer,
        config: ProceduralMemoryConfig,
        min_quality_score: float = _DEFAULT_MIN_QUALITY_SCORE,
    ) -> None:
        self._proposer = proposer
        self._config = config
        self._min_quality_score = min_quality_score

    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "success"

    async def capture(
        self,
        *,
        execution_result: ExecutionResult,
        recovery_result: RecoveryResult | None,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        memory_backend: MemoryBackend,
    ) -> NotBlankStr | None:
        """Capture a procedural memory from a successful execution.

        Only captures when execution succeeded (no recovery) and the
        proposed memory's quality (confidence * 10) meets the threshold.

        Args:
            execution_result: Result of the task execution.
            recovery_result: Recovery result (None if no recovery).
            agent_id: Agent that executed the task.
            task_id: Task that was executed.
            memory_backend: Memory backend for storage.

        Returns:
            Memory entry ID if a memory was captured, None otherwise.
        """
        # Only proceed if execution succeeded (no recovery)
        if recovery_result is not None:
            return None

        # Check if execution was successful
        if execution_result.termination_reason != TerminationReason.COMPLETED:
            return None

        # Propose memory from successful execution
        proposal = await self._proposer.propose(execution_result)
        if proposal is None:
            return None

        # Check quality threshold (confidence * 10)
        quality_score = proposal.confidence * 10.0
        if quality_score < self._min_quality_score:
            logger.info(
                PROCEDURAL_CAPTURE_QUALITY_BELOW_THRESHOLD,
                quality=quality_score,
                min_quality=self._min_quality_score,
                confidence=proposal.confidence,
            )
            return None

        # Store the memory with success-derived tag
        tags = (NON_INFERABLE_TAG, "success-derived", *proposal.tags)
        request = MemoryStoreRequest(
            category=MemoryCategory.PROCEDURAL,
            content=self._format_content(proposal),
            metadata=MemoryMetadata(
                source=f"success:{task_id}",
                confidence=proposal.confidence,
                tags=tags,
            ),
        )

        try:
            memory_id = await memory_backend.store(agent_id, request)
            logger.info(
                PROCEDURAL_CAPTURE_STORED,
                memory_id=memory_id,
                quality=quality_score,
                confidence=proposal.confidence,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROCEDURAL_CAPTURE_STORE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        else:
            return memory_id

    @staticmethod
    def _format_content(proposal: object) -> str:
        """Format a proposal into three-tier progressive disclosure text.

        Returns:
            Result of type ``str``.
        """
        discovery = getattr(proposal, "discovery", "")
        condition = getattr(proposal, "condition", "")
        action = getattr(proposal, "action", "")
        rationale = getattr(proposal, "rationale", "")
        execution_steps = getattr(proposal, "execution_steps", ())

        parts = [
            f"[DISCOVERY] {discovery}",
            f"[CONDITION] {condition}",
            f"[ACTION] {action}",
            f"[RATIONALE] {rationale}",
        ]
        if execution_steps:
            steps = "\n".join(
                f"  {i}. {step}" for i, step in enumerate(execution_steps, 1)
            )
            parts.append(f"[EXECUTION]\n{steps}")
        return "\n\n".join(parts)
