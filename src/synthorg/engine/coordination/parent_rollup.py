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

The subtask statuses this rollup derives from are read from persistence,
never from the run outcomes the dispatcher returns: a run that finished is
not a run that was verified. Immediately after ``coordinate()`` most
subtasks are therefore ``IN_REVIEW`` and the parent stays ``IN_PROGRESS``;
the initiative rollup re-derives it on every later task event, so the parent
lands on its terminal status once the review gate has ruled on each child.
"""

import asyncio
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from synthorg.core.clock import Clock
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.task_transitions import transition_path
from synthorg.engine.coordination._parent_phase_results import (
    ParentUpdateOutcome,
    fail_update_parent_phase,
    record_update_parent_outcome,
    skip_update_parent_phase,
)
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

#: Serialises the two walkers of one parent task: coordination's one-shot
#: advance and the initiative rollup's per-recompute one. Module-level because
#: they are different objects with no shared owner, and the interleave they
#: would otherwise produce is invisible to the engine's per-hop validation.
_PARENT_WALK_LOCKS: Final[RefcountedLockMap[str]] = RefcountedLockMap()


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
        # lint-allow: swallow-ok -- diagnostic note enrichment; return base note
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
    target: TaskStatus | None = None,
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
        target: Status to walk to instead of the rollup-derived one. Lets a
            caller that knows more than the child counts hold the parent
            short of terminal (an initiative whose items are all done is
            still integrating), while the counts in the audit reason stay
            the real ones.

    Two callers walk the same parent: coordination's one-shot advance when
    ``coordinate()`` returns, and the initiative rollup on every recompute.
    Each hop is individually legal, so two interleaved walks would land the
    parent on a status neither derived and the engine's own validation could
    not see it. The whole walk therefore runs under a per-task lock shared by
    both callers, and the path is re-derived from a read taken inside it so
    the loser of the race plans against the winner's result rather than its
    own stale snapshot.

    Returns:
        A :class:`ParentUpdateOutcome` describing success, the failure
        note (if any), and how many hops landed.
    """
    async with _PARENT_WALK_LOCKS.acquire(task_id):
        return await _walk_parent(
            task_engine,
            task_id=task_id,
            current_status=await _live_status(task_engine, task_id, current_status),
            rollup=rollup,
            target=target,
        )


async def _live_status(
    task_engine: TaskEngine,
    task_id: str,
    fallback: TaskStatus,
) -> TaskStatus:
    """Read the parent's status inside the walk lock.

    Returns:
        The persisted status, or *fallback* when the parent cannot be read
        (the walk then fails on its first hop, which is where an unreadable
        parent belongs).
    """
    try:
        live = await task_engine.get_task(task_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- staleness refresh only; the hops below
        # still go through the engine's own transition validation
        reraise_critical(exc)
        return fallback
    return fallback if live is None else live.status


async def _walk_parent(
    task_engine: TaskEngine,
    *,
    task_id: str,
    current_status: TaskStatus,
    rollup: SubtaskStatusRollup,
    target: TaskStatus | None,
) -> ParentUpdateOutcome:
    """Submit each hop from *current_status* to the walk's target.

    Returns:
        A :class:`ParentUpdateOutcome` describing success, the failure note
        (if any), and how many hops landed.
    """
    walk_to = target if target is not None else rollup.derived_parent_status
    path = transition_path(current_status, walk_to)
    if path is None:
        note = (
            f"Parent status {current_status.value!r} cannot reach "
            f"rollup status {walk_to.value!r}: no valid lifecycle path "
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


async def _collect_subtask_statuses(
    task_engine: TaskEngine,
    decomp_result: DecompositionResult,
) -> tuple[TaskStatus, ...]:
    """Read each subtask's persisted status, in plan order.

    Deliberately *not* derived from the ``DispatchResult`` outcomes: those
    report that a run finished, which is true well before it is verified. A
    subtask that executed cleanly is normally ``IN_REVIEW`` at this point and
    only reaches ``COMPLETED`` once the review gate's oracle chain passes, so
    reading the outcome would let the parent complete on unverified work.
    Reading persisted status makes this rollup compose with the gate exactly
    as the initiative rollup does.

    A subtask with no persisted row counts as ``BLOCKED`` rather than
    silently shrinking the total. Being unroutable is not that case:
    coordination files every decomposed child before it routes, so a
    subtask nobody may take still has a row and reads back ``CREATED``,
    which leaves it in the backlog for a later hire at the rung.

    The reads are independent, and a decomposition may hold up to a hundred
    subtasks, so they run concurrently rather than as a hundred sequential
    round trips on the observer's critical path.

    Returns:
        One ``TaskStatus`` per expected subtask, in plan order.
    """
    async with asyncio.TaskGroup() as group:
        reads = [
            group.create_task(task_engine.get_task(subtask.id))
            for subtask in decomp_result.plan.subtasks
        ]
    return tuple(_status_of(read.result()) for read in reads)


def _status_of(task: Task | None) -> TaskStatus:
    """Return *task*'s status, or ``BLOCKED`` when it never reached the engine.

    Returns:
        The persisted status, or ``BLOCKED`` for a missing row.
    """
    return TaskStatus.BLOCKED if task is None else task.status


async def compute_status_rollup(
    *,
    decomposition_service: DecompositionService,
    task_engine: TaskEngine | None,
    clock: Clock,
    context: CoordinationContext,
    decomp_result: DecompositionResult,
    phases: list[CoordinationPhaseResult],
) -> SubtaskStatusRollup | None:
    """Compute and record the subtask status rollup phase.

    Reads each subtask's persisted status, invokes the decomposition service
    to compute the rollup, and owns all ``rollup`` phase bookkeeping:
    monotonic timing, structured logging (start/complete/failure events),
    and accumulation of the phase result.

    Returns:
        ``SubtaskStatusRollup`` on success; ``None`` on failure or when no
        task engine is wired (there is then no persisted status to read). A
        failed computation is recorded as a failed ``CoordinationPhaseResult``
        in the ``phases`` list and a WARNING log entry (so the phase list
        surfaces the failure point without re-raising).
    """
    start = clock.monotonic()
    phase_name = "rollup"
    logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
    if task_engine is None:
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            duration_seconds=0.0,
            note="skipped: no task engine, no persisted status to roll up",
        )
        return None
    try:
        statuses = await _collect_subtask_statuses(task_engine, decomp_result)
        rollup = decomposition_service.rollup_status(str(context.task.id), statuses)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- records rollup phase failure into phases list
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


def _initiative_owns_parent(context: CoordinationContext) -> bool:
    """Report whether a plan, not this run, owns the parent's status.

    The run's own context names the plan that provisioned it, which is what
    the plan-review dispatcher knows and the task row does not: an objective
    task carries no ``plan_id``, because the link lives on the plan
    (``Plan.parent_task_id``), so reading the column alone let the walk go
    ahead on exactly the parents it was meant to leave alone.

    The column is deliberately NOT consulted as a second piece of evidence.
    ``plan_mapping`` stamps ``plan_id`` on every child task a plan creates,
    while the initiative rollup walks ``Plan.parent_task_id`` and nothing
    else, so a plan-item task that is itself a coordination parent would
    match the column, be skipped here, and be written by nobody: it would
    sit IN_PROGRESS for ever and its plan could never conclude. Deferring
    on the context alone leaves exactly one writer in both directions.

    Returns:
        ``True`` when the initiative rollup is the parent's writer.
    """
    return context.plan_id is not None


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

    Ownership of a parent's status is a ladder with one resolver: a
    plan-driven parent belongs to the initiative rollup, which re-derives it
    on every task event, and everything else belongs to this walk, which is
    then the only writer there is. See :func:`skip_update_parent_phase` for
    what the two owners did to one objective when both ran.

    No-op when ``task_engine`` is ``None`` (empty company). Fails the phase
    (not propagating) when the rollup is missing, the parent is gone, or a
    lifecycle hop is rejected (usually concurrent external finalisation).
    """
    if task_engine is None:
        return
    if rollup is None:
        fail_update_parent_phase(
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
            fail_update_parent_phase(
                phases,
                clock=clock,
                error=f"Parent task {str(context.task.id)!r} not found",
                start=start,
            )
            return
        if _initiative_owns_parent(context):
            skip_update_parent_phase(phases, clock=clock, start=start)
            return
        outcome = await advance_parent_to_rollup_status(
            task_engine,
            task_id=str(context.task.id),
            current_status=live_task.status,
            rollup=rollup,
        )
        record_update_parent_outcome(phases, clock=clock, outcome=outcome, start=start)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- records parent-update phase failure into phases list
        reraise_critical(exc)
        fail_update_parent_phase(
            phases,
            clock=clock,
            error=safe_error_description(exc),
            start=start,
            error_type=type(exc).__name__,
        )
