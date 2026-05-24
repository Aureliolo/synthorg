"""Checkpoint resume helpers.

Standalone functions for deserializing checkpoint context,
injecting reconciliation messages, building loop instances with
checkpoint callbacks, and cleaning up after a successful resume.
Used by ``AgentEngine`` to keep resume orchestration concise.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import FailureCategory  # noqa: TC001
from synthorg.engine.checkpoint.callback_factory import make_checkpoint_callback
from synthorg.engine.checkpoint.models import CheckpointConfig  # noqa: TC001
from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.plan_execute_loop import PlanExecuteLoop
from synthorg.engine.react_loop import ReactLoop
from synthorg.engine.sanitization import sanitize_message
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.checkpoint import (
    CHECKPOINT_DELETE_FAILED,
    CHECKPOINT_DELETED,
    CHECKPOINT_RECOVERY_DESERIALIZE_FAILED,
    CHECKPOINT_RECOVERY_RECONCILIATION,
    CHECKPOINT_UNSUPPORTED_LOOP,
    HEARTBEAT_DELETE_FAILED,
    HEARTBEAT_DELETED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.engine.loop_protocol import ExecutionLoop
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )

logger = get_logger(__name__)


def deserialize_and_reconcile(  # noqa: PLR0913
    checkpoint_json: str,
    error_message: str,
    agent_id: str,
    task_id: str,
    *,
    failure_category: FailureCategory,
    criteria_failed: tuple[str, ...] = (),
) -> AgentContext:
    """Deserialize checkpoint context and inject reconciliation message.

    Args:
        checkpoint_json: JSON-serialized ``AgentContext``.
        error_message: The error that triggered recovery (included
            in the reconciliation message so the agent is aware of the
            specific failure that preceded the resume).
        agent_id: Agent identifier (for logging).
        task_id: Task identifier (for logging).
        failure_category: Machine-readable failure classification.
        criteria_failed: Acceptance criteria that were not met.

    Returns:
        Reconstituted ``AgentContext`` with reconciliation message.

    Raises:
        ValueError: If deserialization or schema validation fails
            (includes ``pydantic.ValidationError``).
    """
    try:
        checkpoint_ctx = AgentContext.model_validate_json(checkpoint_json)
    except ValueError as exc:
        log_exception_redacted(
            logger,
            CHECKPOINT_RECOVERY_DESERIALIZE_FAILED,
            exc,
            agent_id=agent_id,
            task_id=task_id,
            reason="Failed to deserialize checkpoint context",
        )
        raise

    compression = checkpoint_ctx.compression_metadata
    compaction_note = (
        f"Note: conversation was previously compacted "
        f"(archived {compression.archived_turns} turns). "
        if compression is not None
        else ""
    )
    # May reduce to "details redacted" if no alphanumeric content remains;
    # leak prevention takes priority over detail in LLM context.
    safe_error = sanitize_message(error_message)
    category_note = f"Failure category: {failure_category.value}. "
    # Criteria descriptions are user- or LLM-authored free text and
    # must be sanitized alongside error_message to keep the reconcile
    # message safe against path/URL leaks and prompt-injection fragments.
    sanitized_criteria = tuple(sanitize_message(c) for c in criteria_failed)
    criteria_note = (
        f"Unmet criteria: {'; '.join(sanitized_criteria)}. "
        if sanitized_criteria
        else ""
    )
    reconciliation_content = (
        f"Execution resumed from checkpoint at turn "
        f"{checkpoint_ctx.turn_count}. {compaction_note}"
        f"{category_note}{criteria_note}"
        f"Previous error: {safe_error}. "
        "Review progress and continue."
    )

    reconciliation_msg = ChatMessage(
        role=MessageRole.SYSTEM,
        content=reconciliation_content,
    )
    logger.debug(
        CHECKPOINT_RECOVERY_RECONCILIATION,
        agent_id=agent_id,
        task_id=task_id,
        turn_count=checkpoint_ctx.turn_count,
    )
    return checkpoint_ctx.with_message(reconciliation_msg)


def make_loop_with_callback(  # noqa: PLR0913
    loop: ExecutionLoop,
    checkpoint_repo: CheckpointRepository | None,
    heartbeat_repo: HeartbeatRepository | None,
    checkpoint_config: CheckpointConfig,
    agent_id: str,
    task_id: str,
) -> ExecutionLoop:
    """Return the execution loop with a checkpoint callback if configured.

    If ``checkpoint_repo`` and ``heartbeat_repo`` are both set,
    creates a checkpoint callback and returns a new loop instance
    with it injected.  Otherwise returns the original loop unchanged.
    """
    if checkpoint_repo is None or heartbeat_repo is None:
        return loop

    callback = make_checkpoint_callback(
        checkpoint_repo=checkpoint_repo,
        heartbeat_repo=heartbeat_repo,
        config=checkpoint_config,
        agent_id=agent_id,
        task_id=task_id,
    )

    if isinstance(loop, ReactLoop):
        return ReactLoop(
            checkpoint_callback=callback,
            approval_gate=loop.approval_gate,
            stagnation_detector=loop.stagnation_detector,
            compaction_callback=loop.compaction_callback,
        )
    if isinstance(loop, PlanExecuteLoop):
        return PlanExecuteLoop(
            config=loop.config,
            checkpoint_callback=callback,
            approval_gate=loop.approval_gate,
            stagnation_detector=loop.stagnation_detector,
            compaction_callback=loop.compaction_callback,
        )
    if isinstance(loop, HybridLoop):
        return HybridLoop(
            config=loop.config,
            checkpoint_callback=callback,
            approval_gate=loop.approval_gate,
            stagnation_detector=loop.stagnation_detector,
            compaction_callback=loop.compaction_callback,
        )
    logger.warning(
        CHECKPOINT_UNSUPPORTED_LOOP,
        loop_type=type(loop).__name__,
        error="Unsupported loop type for checkpoint callback injection",
    )
    return loop


async def cleanup_checkpoint_artifacts(
    checkpoint_repo: CheckpointRepository | None,
    heartbeat_repo: HeartbeatRepository | None,
    execution_id: str,
) -> None:
    """Delete checkpoints and heartbeat for an execution.

    Used after successful resume completion and on fallback paths
    to prevent orphaned rows.

    Best-effort: errors are logged but never propagated.

    Args:
        checkpoint_repo: Checkpoint repository (may be ``None``).
        heartbeat_repo: Heartbeat repository (may be ``None``).
        execution_id: The execution whose data should be cleaned up.
    """
    if checkpoint_repo is not None:
        try:
            count = await checkpoint_repo.delete_by_execution(execution_id)
            logger.debug(
                CHECKPOINT_DELETED,
                execution_id=execution_id,
                deleted_count=count,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                CHECKPOINT_DELETE_FAILED,
                execution_id=execution_id,
                error="Failed to clean up checkpoints after resume",
            )

    if heartbeat_repo is not None:
        try:
            await heartbeat_repo.delete(execution_id)
            logger.debug(
                HEARTBEAT_DELETED,
                execution_id=execution_id,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                HEARTBEAT_DELETE_FAILED,
                execution_id=execution_id,
                error="Failed to clean up heartbeat after resume",
            )
