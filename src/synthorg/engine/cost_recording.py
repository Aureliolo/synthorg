"""Per-turn cost recording for agent execution.

Handles per-turn cost recording from execution results into the
``CostTracker`` service, preserving full per-call granularity and
structured logging.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.budget.cost_record import CostRecord
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_COST_FAILED,
    EXECUTION_ENGINE_COST_RECORDED,
    EXECUTION_ENGINE_COST_SKIPPED,
)

if TYPE_CHECKING:
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.loop_protocol import ExecutionResult, TurnRecord

logger = get_logger(__name__)


async def record_execution_costs(
    result: ExecutionResult,
    identity: AgentIdentity,
    agent_id: str,
    task_id: str,
    *,
    tracker: CostTracker | None,
) -> None:
    """Record per-turn costs to the CostTracker if available.

    Each turn produces its own ``CostRecord``, preserving per-call
    granularity.  Turns with zero cost and zero tokens are skipped.

    Recording failures for regular exceptions are logged but do not
    affect the execution result.  ``MemoryError`` and
    ``RecursionError`` propagate unconditionally as non-recoverable
    system errors.
    """
    if tracker is None:
        logger.debug(
            EXECUTION_ENGINE_COST_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="no cost tracker configured",
        )
        return

    for turn in result.turns:
        # Skip only when provably nothing happened (zero cost and
        # zero tokens); a turn with tokens but zero cost (e.g., a
        # free-tier provider) is still recorded.
        if turn.cost_usd == 0.0 and turn.input_tokens == 0 and turn.output_tokens == 0:
            logger.debug(
                EXECUTION_ENGINE_COST_SKIPPED,
                agent_id=agent_id,
                task_id=task_id,
                turn_number=turn.turn_number,
                reason="zero cost and zero tokens",
            )
            continue

        record = CostRecord(
            agent_id=agent_id,
            task_id=task_id,
            provider=identity.model.provider,
            model=identity.model.model_id,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            cost_usd=turn.cost_usd,
            timestamp=datetime.now(UTC),
            call_category=turn.call_category,
        )
        await _submit_cost_record(
            record,
            turn,
            agent_id,
            task_id,
            tracker=tracker,
        )


async def _submit_cost_record(
    record: CostRecord,
    turn: TurnRecord,
    agent_id: str,
    task_id: str,
    *,
    tracker: CostTracker,
) -> None:
    """Submit a cost record to the tracker, logging failures."""
    try:
        await tracker.record(record)
    except MemoryError, RecursionError:
        logger.error(
            EXECUTION_ENGINE_COST_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            error="non-recoverable error in cost recording",
            exc_info=True,
        )
        raise
    except Exception as exc:
        logger.exception(
            EXECUTION_ENGINE_COST_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            error=f"{type(exc).__name__}: {exc}",
            cost_usd=turn.cost_usd,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )
        return

    logger.info(
        EXECUTION_ENGINE_COST_RECORDED,
        agent_id=agent_id,
        task_id=task_id,
        cost_usd=turn.cost_usd,
    )
