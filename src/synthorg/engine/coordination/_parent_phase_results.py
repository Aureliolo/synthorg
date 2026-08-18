# module-kind: code
"""How the ``update_parent`` coordination phase records what it did.

One phase with four outcomes (walked, already there, skipped because
something else owns the parent, failed), each of which has to reach both the
log an operator reads and the ``CoordinationPhaseResult`` the pipeline
returns. Keeping the recording here leaves the walk itself in
:mod:`parent_rollup` about lifecycle hops.
"""

from typing import NamedTuple

from synthorg.core.clock import Clock
from synthorg.engine.coordination.models import CoordinationPhaseResult
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import (
    COORDINATION_PHASE_COMPLETED,
    COORDINATION_PHASE_FAILED,
)

logger = get_logger(__name__)

_PHASE: str = "update_parent"


class ParentUpdateOutcome(NamedTuple):
    """Result of walking the parent task to its rollup-derived status.

    Attributes:
        success: ``False`` when no valid lifecycle path exists or a hop
            was rejected mid-walk.
        error: Operator-readable note when ``success`` is ``False``; on a
            mid-walk rejection it includes the parent's actual live
            status so concurrent external finalisation is diagnosable.
        hops_completed: Number of transitions that landed (``0`` for an
            already-at-target no-op, which is still ``success=True``).
    """

    success: bool
    error: str | None
    hops_completed: int


def fail_update_parent_phase(
    phases: list[CoordinationPhaseResult],
    *,
    clock: Clock,
    error: str,
    start: float | None,
    error_type: str | None = None,
) -> None:
    """Log and append an ``update_parent`` phase failure to the result list.

    Args:
        phases: Phase result accumulator (mutated in-place).
        clock: Clock for duration measurement.
        error: Operator-readable failure note.
        start: Monotonic clock reading when the phase started, or ``None``
            if the failure occurred before phase timing began (e.g. when
            the rollup computation failed). When ``None``, duration is
            recorded as ``0.0``.
        error_type: Optional exception type name; included in the log if
            provided to distinguish the failure source.
    """
    elapsed = 0.0 if start is None else clock.monotonic() - start
    if error_type is None:
        logger.warning(COORDINATION_PHASE_FAILED, phase=_PHASE, error=error)
    else:
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=_PHASE,
            error_type=error_type,
            error=error,
        )
    phases.append(
        CoordinationPhaseResult(
            phase=_PHASE,
            success=False,
            duration_seconds=elapsed,
            error=error,
        )
    )


def skip_update_parent_phase(
    phases: list[CoordinationPhaseResult],
    *,
    clock: Clock,
    start: float,
) -> None:
    """Record that the initiative rollup owns this parent, so nothing walked.

    A plan-driven parent has exactly one writer, and it is not this one.
    ``advance_objective_task`` re-derives the parent on every task event,
    over plan items rather than one coordination run's subtasks, and holds
    it short of any finished-looking status until the plan itself completes.
    This walk has neither rule, so with both running the two derivations
    disagreed on the same objective in the same instant (0/7 completed
    against 1/8) and the second walked the task back out of the terminal
    status the first had just set.

    A success, not a failure: nothing went wrong and nothing was skipped
    that anyone still needs.
    """
    elapsed = clock.monotonic() - start
    logger.info(
        COORDINATION_PHASE_COMPLETED,
        phase=_PHASE,
        duration_seconds=elapsed,
        hops=0,
        note="plan-driven parent; the initiative rollup owns its status",
    )
    phases.append(
        CoordinationPhaseResult(
            phase=_PHASE,
            success=True,
            duration_seconds=elapsed,
        )
    )


def record_update_parent_outcome(
    phases: list[CoordinationPhaseResult],
    *,
    clock: Clock,
    outcome: ParentUpdateOutcome,
    start: float,
) -> None:
    """Log + append the result of the parent lifecycle walk."""
    elapsed = clock.monotonic() - start
    if outcome.success:
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=_PHASE,
            duration_seconds=elapsed,
            hops=outcome.hops_completed,
        )
    else:
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=_PHASE,
            error=outcome.error,
            hops_completed=outcome.hops_completed,
        )
    phases.append(
        CoordinationPhaseResult(
            phase=_PHASE,
            success=outcome.success,
            duration_seconds=elapsed,
            error=outcome.error,
        )
    )


__all__ = [
    "ParentUpdateOutcome",
    "fail_update_parent_phase",
    "record_update_parent_outcome",
    "skip_update_parent_phase",
]
