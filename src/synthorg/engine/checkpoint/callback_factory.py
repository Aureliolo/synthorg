"""Factory for creating checkpoint callbacks.

Produces a closure that persists checkpoints and heartbeats after
each completed turn.  Errors are logged but never propagated
(best-effort) to avoid crashing the execution loop.
"""

from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.checkpoint.callback import CheckpointCallback  # noqa: TC001
from synthorg.engine.checkpoint.models import (
    Checkpoint,
    CheckpointConfig,
    Heartbeat,
)
from synthorg.engine.context import AgentContext  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.checkpoint import (
    CHECKPOINT_SAVE_FAILED,
    CHECKPOINT_SAVED,
    CHECKPOINT_SKIPPED,
    HEARTBEAT_UPDATE_FAILED,
    HEARTBEAT_UPDATED,
)
from synthorg.persistence.repositories import (
    CheckpointRepository,  # noqa: TC001
    HeartbeatRepository,  # noqa: TC001
)

logger = get_logger(__name__)


def make_checkpoint_callback(
    *,
    checkpoint_repo: CheckpointRepository,
    heartbeat_repo: HeartbeatRepository,
    config: CheckpointConfig,
    agent_id: NotBlankStr,
    task_id: NotBlankStr,
) -> CheckpointCallback:
    """Create a checkpoint callback closure.

    The returned callback:
    1. Skips turn 0 (no work done yet) and non-boundary turns
       where ``turn_count % persist_every_n_turns != 0``.
    2. Serializes the ``AgentContext`` to JSON and saves a checkpoint.
    3. Updates the heartbeat timestamp.
    4. Errors are logged but never propagated (except ``MemoryError``
       and ``RecursionError``).

    Args:
        checkpoint_repo: Repository for persisting checkpoints.
        heartbeat_repo: Repository for persisting heartbeats.
        config: Checkpoint configuration.
        agent_id: Agent identifier for the checkpoint.
        task_id: Task identifier for the checkpoint.

    Returns:
        An async callback suitable for injection into execution loops.
    """

    async def _checkpoint_callback(ctx: AgentContext) -> None:
        turn = ctx.turn_count
        if turn == 0 or turn % config.persist_every_n_turns != 0:
            logger.debug(
                CHECKPOINT_SKIPPED,
                execution_id=ctx.execution_id,
                turn_number=turn,
                persist_every_n_turns=config.persist_every_n_turns,
            )
            return

        checkpoint_saved = await _save_checkpoint(ctx, turn)
        if checkpoint_saved:
            await _save_heartbeat(ctx)

    async def _save_checkpoint(ctx: AgentContext, turn: int) -> bool:
        """Persist checkpoint (best-effort). Return True on success."""
        try:
            checkpoint = Checkpoint(
                execution_id=ctx.execution_id,
                agent_id=agent_id,
                task_id=task_id,
                turn_number=turn,
                context_json=ctx.model_dump_json(),
            )
            await checkpoint_repo.save(checkpoint)
            logger.info(
                CHECKPOINT_SAVED,
                execution_id=ctx.execution_id,
                turn_number=turn,
                checkpoint_id=checkpoint.id,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                CHECKPOINT_SAVE_FAILED,
                execution_id=ctx.execution_id,
                turn_number=turn,
            )
            return False
        return True

    async def _save_heartbeat(ctx: AgentContext) -> None:
        """Update heartbeat (best-effort).

        Only called after checkpoint save succeeds, preventing the
        limbo state where a fresh heartbeat exists but there is no
        checkpoint to resume from.
        """
        try:
            heartbeat = Heartbeat(
                execution_id=ctx.execution_id,
                agent_id=agent_id,
                task_id=task_id,
                last_heartbeat_at=datetime.now(UTC),
            )
            await heartbeat_repo.save(heartbeat)
            logger.debug(
                HEARTBEAT_UPDATED,
                execution_id=ctx.execution_id,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                HEARTBEAT_UPDATE_FAILED,
                execution_id=ctx.execution_id,
            )

    return _checkpoint_callback
