"""Loop-gating helpers for all ExecutionLoop implementations.

Stateless control-flow gates shared by the loops: shutdown and budget
checks, stagnation detection and verdict handling, and compaction
invocation. Each function takes explicit parameters and returns an
``ExecutionResult`` (terminate) or ``None`` (continue). Result
construction is delegated to :func:`loop_helpers.build_result`.

This module is stateless control flow only; the ``wrap_untrusted``
fence responsibility lives upstream of the loop (see
:mod:`synthorg.engine.loop_helpers` for the full wrap-ownership note).
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_COMPACTION_FAILED,
)
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_BUDGET_EXHAUSTED,
    EXECUTION_LOOP_ERROR,
    EXECUTION_LOOP_SHUTDOWN,
)
from synthorg.observability.events.stagnation import (
    STAGNATION_CORRECTION_INJECTED,
    STAGNATION_TERMINATED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

from .loop_helpers import build_result
from .loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TerminationReason,
    TurnRecord,
)
from .stagnation.models import StagnationResult, StagnationVerdict

if TYPE_CHECKING:
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.context import AgentContext
    from synthorg.engine.stagnation.protocol import StagnationDetector

logger = get_logger(__name__)


def check_shutdown(
    ctx: AgentContext,
    shutdown_checker: ShutdownChecker | None,
    turns: list[TurnRecord],
) -> ExecutionResult | None:
    """Return a SHUTDOWN result if a shutdown has been requested.

    Args:
        ctx: Current agent context.
        shutdown_checker: Optional callback returning ``True`` on shutdown.
        turns: Accumulated turn records.

    Returns:
        ``ExecutionResult`` with SHUTDOWN reason, or ``None`` to continue.
    """
    if shutdown_checker is None:
        return None
    try:
        shutting_down = shutdown_checker()
    except Exception as exc:
        reraise_critical(exc)
        error_msg = f"Shutdown checker failed: {type(exc).__name__}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=ctx.turn_count,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return build_result(
            ctx,
            TerminationReason.ERROR,
            turns,
            error_message=error_msg,
        )
    if not shutting_down:
        return None
    logger.info(
        EXECUTION_LOOP_SHUTDOWN,
        execution_id=ctx.execution_id,
        turn=ctx.turn_count,
    )
    return build_result(ctx, TerminationReason.SHUTDOWN, turns)


def check_budget(
    ctx: AgentContext,
    budget_checker: BudgetChecker | None,
    turns: list[TurnRecord],
) -> ExecutionResult | None:
    """Return a BUDGET_EXHAUSTED result if budget is exhausted.

    Args:
        ctx: Current agent context.
        budget_checker: Optional callback returning ``True`` on exhaustion.
        turns: Accumulated turn records.

    Returns:
        ``ExecutionResult`` with BUDGET_EXHAUSTED reason, or ``None``.
    """
    if budget_checker is None:
        return None
    try:
        exhausted = budget_checker(ctx)
    except Exception as exc:
        reraise_critical(exc)
        error_msg = f"Budget checker failed: {type(exc).__name__}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            EXECUTION_LOOP_ERROR,
            execution_id=ctx.execution_id,
            turn=ctx.turn_count,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return build_result(
            ctx,
            TerminationReason.ERROR,
            turns,
            error_message=error_msg,
        )
    if exhausted:
        logger.warning(
            EXECUTION_LOOP_BUDGET_EXHAUSTED,
            execution_id=ctx.execution_id,
            turn=ctx.turn_count,
        )
        return build_result(
            ctx,
            TerminationReason.BUDGET_EXHAUSTED,
            turns,
        )
    return None


async def check_stagnation(  # noqa: PLR0913
    ctx: AgentContext,
    stagnation_detector: StagnationDetector | None,
    turns: list[TurnRecord],
    corrections_injected: int,
    *,
    execution_id: str,
    step_number: int | None = None,
) -> tuple[AgentContext, int] | ExecutionResult | None:
    """Run stagnation detection and handle the verdict.

    Stagnation detection is advisory -- detector failures are logged
    and skipped so they never interrupt an otherwise-healthy loop.

    Args:
        ctx: Current agent context.
        stagnation_detector: Optional detector; ``None`` skips the
            check.
        turns: Accumulated turn records from the current scope.
        corrections_injected: Number of corrective prompts already
            injected in this execution scope.
        execution_id: Execution identifier for structured logging.
        step_number: Optional step number for plan-and-execute loops
            (included in log entries and termination metadata).

    Returns:
        ``None`` to continue the loop (no stagnation).
        ``(ctx, corrections_injected)`` when a corrective prompt was
        injected (caller should use the updated values).
        ``ExecutionResult`` with STAGNATION reason to terminate.

    Raises:
        MemoryError: Re-raised unconditionally.
        RecursionError: Re-raised unconditionally.
    """
    if stagnation_detector is None:
        return None

    try:
        stag = await stagnation_detector.check(
            tuple(turns),
            corrections_injected=corrections_injected,
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            EXECUTION_LOOP_ERROR,
            execution_id=execution_id,
            turn=ctx.turn_count,
            note="stagnation_check_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    return _handle_stagnation_verdict(
        ctx,
        stag,
        turns,
        corrections_injected,
        execution_id=execution_id,
        step_number=step_number,
    )


def _handle_stagnation_verdict(  # noqa: PLR0913
    ctx: AgentContext,
    stag: StagnationResult,
    turns: list[TurnRecord],
    corrections_injected: int,
    *,
    execution_id: str,
    step_number: int | None = None,
) -> tuple[AgentContext, int] | ExecutionResult | None:
    """Dispatch on the stagnation verdict.

    Args:
        ctx: Current agent context.
        stag: Result from the stagnation detector.
        turns: Accumulated turn records from the current scope.
        corrections_injected: Corrections already injected.
        execution_id: Execution identifier for structured logging.
        step_number: Optional step number for plan-and-execute loops.

    Returns:
        Same semantics as :func:`check_stagnation`.
    """
    if stag.verdict == StagnationVerdict.TERMINATE:
        metadata: dict[str, object] = {"stagnation": stag.model_dump()}
        if step_number is not None:
            metadata["step_number"] = step_number
        logger.warning(
            STAGNATION_TERMINATED,
            execution_id=execution_id,
            step_number=step_number,
            repetition_ratio=stag.repetition_ratio,
            cycle_length=stag.cycle_length,
            corrections_injected=corrections_injected,
        )
        return build_result(
            ctx,
            TerminationReason.STAGNATION,
            turns,
            metadata=metadata,
        )

    if stag.verdict == StagnationVerdict.INJECT_PROMPT:
        logger.info(
            STAGNATION_CORRECTION_INJECTED,
            execution_id=execution_id,
            step_number=step_number,
            repetition_ratio=stag.repetition_ratio,
            correction_number=corrections_injected + 1,
        )
        ctx = ctx.with_message(
            ChatMessage(
                role=MessageRole.USER,
                content=stag.corrective_message,
            ),
        )
        return ctx, corrections_injected + 1

    return None


async def invoke_compaction(
    ctx: AgentContext,
    compaction_callback: CompactionCallback | None,
    turn_number: int,
) -> AgentContext | None:
    """Invoke compaction callback if configured.

    Errors are logged but never propagated -- compaction must
    not interrupt execution.

    Args:
        ctx: Current agent context.
        compaction_callback: Optional compaction callback.
        turn_number: Current turn number for logging.

    Returns:
        Compacted context, or ``None`` if no compaction occurred.

    Raises:
        MemoryError: Re-raised unconditionally.
        RecursionError: Re-raised unconditionally.
    """
    if compaction_callback is None:
        return None
    try:
        return await compaction_callback(ctx)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            CONTEXT_BUDGET_COMPACTION_FAILED,
            execution_id=ctx.execution_id,
            turn=turn_number,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
