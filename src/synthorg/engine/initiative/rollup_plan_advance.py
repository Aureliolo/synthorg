# module-kind: code
"""Walking a plan to its derived status under contention.

Split out of ``rollup.py``: the retry-and-re-derive logic is a self-contained
concern with its own failure taxonomy (a refused transition is a bug, a lost
race is ordinary contention), and it reads better beside the plan state
machine than inside the rollup's event handling.
"""

from dataclasses import dataclass
from typing import Final

from synthorg.core.domain_errors import ConflictError, VersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import TERMINAL_STATUSES, PlanStatus
from synthorg.core.plan_transitions import transition_path
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import derive_plan_status
from synthorg.engine.initiative.item_progress import collect_item_progress
from synthorg.engine.initiative.ports import PlanStatusWriter
from synthorg.engine.initiative.project_writes import MAX_WRITE_ATTEMPTS
from synthorg.observability import get_logger
from synthorg.observability.events.project import (
    PROJECT_ROLLUP_CONFLICT_EXHAUSTED,
    PROJECT_ROLLUP_CONFLICT_RETRY,
    PROJECT_ROLLUP_SKIPPED,
)
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

ROLLUP_ACTOR: Final[str] = "initiative-rollup"


@dataclass(frozen=True, slots=True)
class _ConflictOutcome:
    """What a lost write race left behind, and what to do about it.

    Attributes:
        plan: The plan as the winner left it, or ``None`` when it vanished
            between the failed write and the re-read.
        retry_target: Where the next attempt should aim. ``None`` means the
            winner already settled the question and ``plan`` is the answer,
            so there is nothing left to write.
        retry_reason: The failure reason the next attempt carries, dropped
            whenever the target was re-derived rather than kept.
    """

    plan: Plan | None
    retry_target: PlanStatus | None
    retry_reason: NotBlankStr | None


async def _resolve_conflict(
    persistence: PersistenceBackend,
    current: Plan,
    *,
    attempt: int,
    explicit_target: PlanStatus,
    explicit_reason: NotBlankStr | None,
) -> _ConflictOutcome:
    """Re-read a contended plan and decide what the next attempt writes.

    Args:
        persistence: Backend used to re-read the contended plan.
        current: The plan as this attempt believed it to be.
        attempt: 1-based attempt number, for the retry log line.
        explicit_target: The target the caller originally asked for, which
            distinguishes a caller-driven tail hop from a derived one.
        explicit_reason: The failure reason the caller supplied.

    Returns:
        The outcome the retry loop acts on.
    """
    logger.info(
        PROJECT_ROLLUP_CONFLICT_RETRY,
        plan_id=str(current.id),
        attempt=attempt,
        operation="plan_status",
    )
    refreshed = await persistence.plans.get(NotBlankStr(str(current.id)))
    if refreshed is None:
        return _ConflictOutcome(plan=None, retry_target=None, retry_reason=None)
    if refreshed.status in TERMINAL_STATUSES:
        # The winner finished the plan; its state is authoritative and the
        # caller's project reconcile runs against it.
        return _ConflictOutcome(plan=refreshed, retry_target=None, retry_reason=None)
    if (
        explicit_target is PlanStatus.EVALUATING
        and refreshed.status is PlanStatus.INTEGRATING
    ):
        # ``derive_plan_status`` never emits EVALUATING, so re-deriving here
        # would collapse an explicit INTEGRATING -> EVALUATING write back to
        # INTEGRATING and skip the evaluate stage. The winner left the plan at
        # INTEGRATING, so the caller's tail target is still legal.
        target = PlanStatus.EVALUATING
        reason = explicit_reason
    else:
        items = await collect_item_progress(persistence, refreshed)
        target = derive_plan_status(items, current=refreshed.status)
        # The re-derivation never produces FAILED, so the reason the caller
        # supplied no longer describes this write.
        reason = None
    if target is refreshed.status:
        return _ConflictOutcome(plan=refreshed, retry_target=None, retry_reason=None)
    return _ConflictOutcome(plan=refreshed, retry_target=target, retry_reason=reason)


async def advance_plan(
    persistence: PersistenceBackend,
    plan_writer: PlanStatusWriter,
    plan: Plan,
    target: PlanStatus,
    *,
    failure_reason: NotBlankStr | None = None,
) -> Plan | None:
    """Persist the plan's derived status through the audited write path.

    The target may be several legal hops away, so it is walked rather than
    jumped, exactly as ``advance_project_status`` walks the project. A plan
    that never reached EXECUTING (its dispatch-time sync lost its race)
    completes through EXECUTING rather than attempting the illegal
    ``APPROVED -> COMPLETED`` jump, so the initiative recovers instead of
    stalling one hop short.

    A refused transition and a lost race are different failures and are
    handled differently. ``ConflictError`` means the derivation produced a
    target the state machine rejects even hop by hop, which is a bug:
    retrying reproduces it, so it is surfaced at ERROR and abandoned. A
    version conflict is ordinary contention, so the plan is re-read, the
    target re-derived from the winner's state, and the write retried.

    Args:
        persistence: Backend used to re-read a contended plan.
        plan_writer: The audited plan status writer.
        plan: The plan to move.
        target: Where it should end up.
        failure_reason: Required when *target* is FAILED, which the plan
            model refuses without one so Plan Review always shows why.
            A contended retry re-derives its target and drops this, so a
            re-derived FAILED never lands reasonless.

    Returns:
        The persisted plan, or ``None`` when the transition was refused or
        the write stayed contended for the whole retry budget.
    """
    current = plan
    explicit_target = target
    explicit_reason = failure_reason
    for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
        try:
            return await walk_plan_to(
                plan_writer, current, target, failure_reason=explicit_reason
            )
        except VersionConflictError:
            # Must precede the ConflictError handler: VersionConflictError
            # subclasses it, so catching the base first would strand every
            # version conflict in the illegal-transition branch and the
            # CAS retry below would never run.
            outcome = await _resolve_conflict(
                persistence,
                current,
                attempt=attempt,
                explicit_target=explicit_target,
                explicit_reason=explicit_reason,
            )
            if outcome.plan is None or outcome.retry_target is None:
                return outcome.plan
            current = outcome.plan
            target = outcome.retry_target
            explicit_reason = outcome.retry_reason
        except ConflictError as exc:
            logger.error(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(current.id),
                current_state=current.status.value,
                target_state=target.value,
                reason="illegal_transition",
                error_type=type(exc).__name__,
            )
            return None
    logger.warning(
        PROJECT_ROLLUP_CONFLICT_EXHAUSTED,
        plan_id=str(plan.id),
        operation="plan_status",
        attempts=MAX_WRITE_ATTEMPTS,
    )
    return None


async def walk_plan_to(
    plan_writer: PlanStatusWriter,
    plan: Plan,
    target: PlanStatus,
    *,
    failure_reason: NotBlankStr | None = None,
) -> Plan:
    """Move *plan* to *target* one legal hop at a time.

    *failure_reason* rides only the FAILED hop: the plan model requires one
    exactly there and rejects it everywhere else.

    Args:
        plan_writer: The audited plan status writer.
        plan: The plan to move.
        target: Where it should end up.
        failure_reason: Carried onto the FAILED hop only.

    Returns:
        The plan after the final hop.

    Raises:
        ConflictError: *target* is unreachable from the plan's status.
        VersionConflictError: A concurrent write won a hop.
    """
    path = transition_path(plan.status, target)
    if path is None:
        msg = f"Plan {plan.id} cannot reach {target.value} from {plan.status.value}"
        raise ConflictError(msg)
    current = plan
    for hop in path:
        current = await plan_writer.sync_status(
            current,
            hop,
            requested_by=ROLLUP_ACTOR,
            failure_reason=(failure_reason if hop is PlanStatus.FAILED else None),
        )
    return current


__all__ = ["ROLLUP_ACTOR", "advance_plan", "walk_plan_to"]
