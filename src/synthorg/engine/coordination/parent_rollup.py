"""Parent-task rollup: walk the parent to its rollup-derived status.

Extracted from the coordination service so the lifecycle-walk logic is
unit-testable in isolation and the service stays within the file/method
size budget.

The parent task may have advanced since the coordination context was
captured, and the rollup-derived status is often several valid hops away
(a freshly CREATED parent must pass through ASSIGNED then IN_PROGRESS
before any terminal status; a fully-completed coordination must pass
through IN_REVIEW before COMPLETED). A single blind transition would be
rejected by the task state machine, so the shortest valid path is walked
hop by hop.
"""

from typing import TYPE_CHECKING, Final, NamedTuple
from uuid import uuid4

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.core.task_transitions import transition_path
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
)
from synthorg.engine.decomposition.models import (
    DecompositionResult,
    SubtaskStatusRollup,
)
from synthorg.engine.task_engine_models import TransitionTaskMutation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_PHASE_COMPLETED,
    COORDINATION_PHASE_FAILED,
    COORDINATION_PHASE_STARTED,
)

if TYPE_CHECKING:
    # Concrete services faked in tests; a runtime import would make typeguard
    # enforce a nominal isinstance the fakes cannot satisfy.
    from synthorg.engine.decomposition.service import DecompositionService
    from synthorg.engine.task_engine import TaskEngine

logger = get_logger(__name__)

# Requester recorded on coordinator-driven parent-task transitions, and
# the synthetic assignee stamped on the parent when the lifecycle forces
# it through ASSIGNED (the parent is owned by the coordinating context,
# not a single agent -- subtasks carry the real per-agent assignments).
COORDINATOR_ACTOR: Final[str] = "coordinator"


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


def _hop_overrides(hop: TaskStatus) -> dict[str, object]:
    """Per-hop mutation overrides.

    The Task model requires a non-null ``assigned_to`` for ASSIGNED (it
    then persists across later hops). The coordinated parent has no
    single owning agent, so stamp the coordinator sentinel on the forced
    ASSIGNED hop only.

    Returns:
        ``{"assigned_to": COORDINATOR_ACTOR}`` for the ASSIGNED hop;
        an empty dict for every other status (no overrides needed).
    """
    if hop is TaskStatus.ASSIGNED:
        return {"assigned_to": COORDINATOR_ACTOR}
    return {}


async def _hop_failure_note(
    task_engine: TaskEngine,
    *,
    task_id: str,
    target_hop: TaskStatus,
    submit_error: str | None,
) -> str:
    """Build a diagnostic note for a rejected lifecycle hop.

    The task-engine submit seam validates every transition, so a
    mid-walk rejection is almost always concurrent external finalisation
    of the parent. The real failure (``submit_error``) is always reported;
    this best-effort re-read only adds the parent's actual live status so
    an operator can see why the hop was rejected.

    A re-read failure is a benign diagnostic-only step: it must not mask
    the original error, so the catch is intentionally narrow (only
    ``MemoryError`` and ``RecursionError`` propagate per project
    convention; all other exceptions are swallowed so the base failure
    note is returned unchanged).

    Returns:
        A short operator-readable note describing the failed hop and,
        when a re-read succeeds, the parent's live status.
    """
    base = (
        f"Parent hop to {target_hop.value!r} rejected: "
        f"{submit_error or 'unknown error'}"
    )
    try:
        live = await task_engine.get_task(task_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        return base
    if live is None:
        return f"{base} (parent no longer found)"
    return f"{base} (parent now {live.status.value!r})"


async def advance_parent_to_rollup_status(
    task_engine: TaskEngine,
    *,
    task_id: str,
    current_status: TaskStatus,
    rollup: SubtaskStatusRollup,
) -> ParentUpdateOutcome:
    """Walk the parent task to ``rollup.derived_parent_status``.

    Each intermediate hop carries a lifecycle-advance reason so the
    status history stays legible; only the final hop carries the rollup
    summary. Returns a no-op success when the parent is already at the
    derived status (empty path). On a hop rejection the parent stays at
    the last successfully applied (individually valid) hop -- a logged
    partial advance, not corruption, since the submit seam validates
    every transition.

    Args:
        task_engine: The task engine seam (validates each transition).
        task_id: The parent task id.
        current_status: The parent's live status (already re-read by the
            caller, so the path starts from reality not a stale snapshot).
        rollup: The subtask status rollup; supplies the derived parent
            status and the completed/failed counts for the final reason.

    Returns:
        A :class:`ParentUpdateOutcome` describing success, the failure
        note (if any), and how many hops landed.
    """
    target = rollup.derived_parent_status
    path = transition_path(current_status, target)
    if path is None:
        note = (
            f"Parent status {current_status.value!r} cannot reach "
            f"rollup status {target.value!r}: no valid lifecycle path "
            f"(parent already terminal or externally finalised)"
        )
        return ParentUpdateOutcome(success=False, error=note, hops_completed=0)

    rollup_reason = (
        f"Coordination rollup: "
        f"{rollup.completed}/{rollup.total} completed, "
        f"{rollup.failed}/{rollup.total} failed"
    )
    completed_hops = 0
    for index, hop in enumerate(path):
        # Every hop except the last carries the generic
        # "Coordination lifecycle advance" reason; only the final hop
        # (the one that lands the parent on the rollup-derived status)
        # carries the completed/failed rollup summary.
        is_final = index == len(path) - 1
        mutation = TransitionTaskMutation(
            request_id=str(uuid4()),
            requested_by=COORDINATOR_ACTOR,
            task_id=task_id,
            target_status=hop,
            reason=(rollup_reason if is_final else "Coordination lifecycle advance"),
            overrides=_hop_overrides(hop),
        )
        result = await task_engine.submit(mutation)
        if not result.success:
            note = await _hop_failure_note(
                task_engine,
                task_id=task_id,
                target_hop=hop,
                submit_error=result.error,
            )
            return ParentUpdateOutcome(
                success=False,
                error=note,
                hops_completed=completed_hops,
            )
        completed_hops += 1

    return ParentUpdateOutcome(
        success=True,
        error=None,
        hops_completed=completed_hops,
    )


def _collect_subtask_statuses(
    dispatch_result: DispatchResult,
    decomp_result: DecompositionResult,
) -> tuple[TaskStatus, ...]:
    """Map execution outcomes to per-subtask statuses, including unexecuted.

    Walks the dispatch result waves and extracts a success/failure status
    for each executed subtask. Subtasks missing from the waves (unroutable,
    blocked by prerequisites, or skipped by fail-fast) are marked as BLOCKED
    so the rollup statuses reflect the complete set of expected subtasks,
    not only those that reached execution.

    Returns:
        Tuple of ``TaskStatus`` values, one per expected subtask in plan
        order: ``COMPLETED`` (executed successfully), ``FAILED`` (executed
        but raised), or ``BLOCKED`` (never executed).
    """
    statuses: list[TaskStatus] = []
    for wave in dispatch_result.waves:
        if wave.execution_result is None:
            statuses.extend(TaskStatus.BLOCKED for _ in wave.subtask_ids)
            continue
        statuses.extend(
            TaskStatus.COMPLETED if outcome.is_success else TaskStatus.FAILED
            for outcome in wave.execution_result.outcomes
        )
    missing_count = len(decomp_result.plan.subtasks) - len(statuses)
    if missing_count > 0:
        statuses.extend(TaskStatus.BLOCKED for _ in range(missing_count))
    return tuple(statuses)


def compute_status_rollup(  # noqa: PLR0913
    *,
    decomposition_service: DecompositionService,
    clock: Clock,
    context: CoordinationContext,
    dispatch_result: DispatchResult,
    decomp_result: DecompositionResult,
    phases: list[CoordinationPhaseResult],
) -> SubtaskStatusRollup | None:
    """Compute and record the subtask status rollup phase.

    Collects subtask execution outcomes, invokes the decomposition service
    to compute the rollup, and owns all ``rollup`` phase bookkeeping:
    monotonic timing, structured logging (start/complete/failure events),
    and accumulation of the phase result.

    Returns:
        ``SubtaskStatusRollup`` on success; ``None`` on failure. A failed
        computation is recorded as a failed ``CoordinationPhaseResult`` in
        the ``phases`` list and a WARNING log entry (so the phase list
        surfaces the failure point without re-raising).
    """
    start = clock.monotonic()
    phase_name = "rollup"
    logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
    try:
        statuses = _collect_subtask_statuses(dispatch_result, decomp_result)
        rollup = decomposition_service.rollup_status(str(context.task.id), statuses)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        elapsed = clock.monotonic() - start
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=phase_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        phases.append(
            CoordinationPhaseResult(
                phase=phase_name,
                success=False,
                duration_seconds=elapsed,
                error=safe_error_description(exc),
            )
        )
        return None

    elapsed = clock.monotonic() - start
    phases.append(
        CoordinationPhaseResult(
            phase=phase_name,
            success=True,
            duration_seconds=elapsed,
        )
    )
    logger.info(
        COORDINATION_PHASE_COMPLETED,
        phase=phase_name,
        duration_seconds=elapsed,
    )
    return rollup


def _fail_update_parent_phase(
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
        logger.warning(COORDINATION_PHASE_FAILED, phase="update_parent", error=error)
    else:
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase="update_parent",
            error_type=error_type,
            error=error,
        )
    phases.append(
        CoordinationPhaseResult(
            phase="update_parent",
            success=False,
            duration_seconds=elapsed,
            error=error,
        )
    )


def _record_update_parent_outcome(
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
            phase="update_parent",
            duration_seconds=elapsed,
            hops=outcome.hops_completed,
        )
    else:
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase="update_parent",
            error=outcome.error,
            hops_completed=outcome.hops_completed,
        )
    phases.append(
        CoordinationPhaseResult(
            phase="update_parent",
            success=outcome.success,
            duration_seconds=elapsed,
            error=outcome.error,
        )
    )


async def run_update_parent_phase(
    *,
    task_engine: TaskEngine | None,
    clock: Clock,
    context: CoordinationContext,
    rollup: SubtaskStatusRollup | None,
    phases: list[CoordinationPhaseResult],
) -> None:
    """Walk the parent task to its rollup-derived status (phase wrapper).

    The ``update_parent`` phase advances the parent from its current status
    to the status derived from the subtask rollup, traversing any required
    intermediate lifecycle hops. No exceptions propagate: failures are
    recorded as failed ``CoordinationPhaseResult`` entries so the
    coordination pipeline completes even when parent update is unavailable.

    No-op when ``task_engine`` is ``None`` (empty company). Fails the phase
    (not propagating) when the rollup is missing, the parent is gone, or a
    lifecycle hop is rejected (usually concurrent external finalisation).
    """
    if task_engine is None:
        return
    if rollup is None:
        _fail_update_parent_phase(
            phases,
            clock=clock,
            error="Skipped -- rollup is None (rollup phase failed)",
            start=None,
        )
        return

    start = clock.monotonic()
    logger.info(COORDINATION_PHASE_STARTED, phase="update_parent")
    try:
        live_task = await task_engine.get_task(str(context.task.id))
        if live_task is None:
            _fail_update_parent_phase(
                phases,
                clock=clock,
                error=f"Parent task {str(context.task.id)!r} not found",
                start=start,
            )
            return
        outcome = await advance_parent_to_rollup_status(
            task_engine,
            task_id=str(context.task.id),
            current_status=live_task.status,
            rollup=rollup,
        )
        _record_update_parent_outcome(phases, clock=clock, outcome=outcome, start=start)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _fail_update_parent_phase(
            phases,
            clock=clock,
            error=safe_error_description(exc),
            start=start,
            error_type=type(exc).__name__,
        )
