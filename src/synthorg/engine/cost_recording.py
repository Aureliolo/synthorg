"""Per-turn cost recording for agent execution.

Handles per-turn cost recording from execution results into the
``CostTracker`` service, preserving full per-call granularity and
structured logging.
"""

from datetime import UTC, datetime

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.budget.tracker import CostTracker
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_COST_FAILED,
    EXECUTION_ENGINE_COST_RECORDED,
    EXECUTION_ENGINE_COST_SKIPPED,
)
from synthorg.observability.metrics_hub import record_provider_usage

logger = get_logger(__name__)


def resolve_tracker_currency(tracker: CostTracker | None) -> CurrencyCode:
    """Resolve the currency code from a CostTracker's BudgetConfig.

    Falls back to ``DEFAULT_CURRENCY`` when no tracker is wired or its
    budget config is unset.  Used by every site that constructs an
    ``AgentRunResult`` so the result carries the same currency as the
    cost rows the tracker emits.

    Returns:
        The configured :class:`CurrencyCode`, or
        :data:`DEFAULT_CURRENCY` when no tracker / budget config is
        available.
    """
    budget_config = getattr(tracker, "budget_config", None)
    if budget_config is None:
        return DEFAULT_CURRENCY
    currency: CurrencyCode = budget_config.currency
    return currency


async def record_execution_costs(  # noqa: PLR0913
    result: ExecutionResult,
    identity: AgentIdentity,
    agent_id: str,
    task_id: str,
    *,
    tracker: CostTracker | None,
    project_id: NotBlankStr | None = None,
) -> None:
    """Record per-turn costs to the CostTracker if available.

    Each turn produces its own ``CostRecord``, preserving per-call
    granularity.  Turns with zero cost and zero tokens are skipped.

    Recording failures for regular exceptions are logged but do not
    affect the execution result.

    Raises:
        MemoryError: Re-raised unconditionally via
            :func:`reraise_critical` (non-recoverable).
        RecursionError: Re-raised unconditionally via
            :func:`reraise_critical` (non-recoverable).
    """
    if tracker is None:
        logger.debug(
            EXECUTION_ENGINE_COST_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="no cost tracker configured",
        )
        return

    currency = resolve_tracker_currency(tracker)

    for turn in result.turns:
        # Skip only when provably nothing happened (zero cost and
        # zero tokens); a turn with tokens but zero cost (e.g., a
        # free-tier provider) is still recorded.
        if turn.cost == 0.0 and turn.input_tokens == 0 and turn.output_tokens == 0:
            logger.debug(
                EXECUTION_ENGINE_COST_SKIPPED,
                agent_id=agent_id,
                task_id=task_id,
                turn_number=turn.turn_number,
                reason="zero cost and zero tokens",
            )
            continue

        try:
            record = CostRecord(
                agent_id=agent_id,
                task_id=task_id,
                project_id=project_id,
                provider=identity.model.provider,
                model=identity.model.model_id,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                cost=turn.cost,
                currency=currency,
                timestamp=datetime.now(UTC),
                call_category=turn.call_category,
                latency_ms=turn.latency_ms,
                cache_hit=turn.cache_hit,
                retry_count=turn.retry_count,
                retry_reason=turn.retry_reason,
                finish_reason=turn.finish_reason,
                success=turn.success,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Validator rejection (e.g. negative cost, blank identifier)
            # would otherwise bubble up and abort the whole recording
            # pass. This function documents recording failures as
            # logged-and-suppressed -- keep that contract for
            # construction errors too.
            logger.warning(
                EXECUTION_ENGINE_COST_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                turn_number=turn.turn_number,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="cost_record_construction_failed",
                cost=turn.cost,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
            )
            continue
        persisted = await _submit_cost_record(
            record,
            turn,
            agent_id,
            task_id,
            tracker=tracker,
        )
        if not persisted:
            # Tracker failed to store the record; skipping the
            # metrics mirror keeps Prometheus counters consistent
            # with the authoritative CostTracker state instead of
            # double-counting costs that never landed.
            continue
        # Mirror the persisted cost record to the Prometheus
        # collector so ``synthorg_provider_tokens_total`` /
        # ``synthorg_provider_cost_total`` reflect every paid
        # completion. No-op when no collector is wired. Metrics
        # failures are caught locally so a prometheus label / push
        # regression cannot turn a successful persisted cost into a
        # visible caller failure -- ``metrics_hub`` already swallows
        # collector exceptions, but the extra guard documents intent
        # and keeps defence-in-depth if that contract changes.
        try:
            record_provider_usage(
                provider=identity.model.provider,
                model=identity.model.model_id,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                cost=turn.cost,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EXECUTION_ENGINE_COST_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                provider=identity.model.provider,
                model=identity.model.model_id,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                cost=turn.cost,
                reason="metrics_mirror_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


async def _submit_cost_record(
    record: CostRecord,
    turn: TurnRecord,
    agent_id: str,
    task_id: str,
    *,
    tracker: CostTracker,
) -> bool:
    """Submit a cost record to the tracker, logging failures.

    Returns:
        ``True`` when the tracker accepted the record, ``False``
        when recording failed. Callers gate downstream mirrors
        (e.g. Prometheus metrics) on the return value so they do
        not double-count costs that never actually landed.
    """
    try:
        await tracker.record(record)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_COST_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            cost=turn.cost,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )
        return False

    logger.info(
        EXECUTION_ENGINE_COST_RECORDED,
        agent_id=agent_id,
        task_id=task_id,
        cost=turn.cost,
        project_id=record.project_id,
    )
    return True
