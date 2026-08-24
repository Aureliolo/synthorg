# module-kind: code
"""The sprint lifecycle tail: ACTIVE -> IN_REVIEW -> RETROSPECTIVE -> COMPLETED.

Extracted from :class:`SprintService` because it has two callers, not one.
The service drives it off a completion event, and
:class:`~synthorg.engine.workflow.sprint_recovery.SprintRecoveryReconciler`
drives it off a cadence for the sprints an event never reached: a process
that died between the backlog write and the spawned tail leaves a
fully-delivered sprint with no completion left to re-fire. A copy in each
caller would be two answers to "is this sprint finished", so there is one.

Every hop is a :meth:`SprintRepository.transition_if` compare-and-set, which
is what makes the two callers safe to run at once: whichever gets there
first moves the row, and the other's CAS finds a state it did not expect and
declines. Nothing here holds a lock, and nothing here needs one -- a lock
would only serialise callers within one process, which is the guarantee this
module deliberately does not depend on.

Nothing here writes ``completed_task_ids``. The tail reads delivery and
moves the lifecycle; it can advance a sprint that was already delivered, but
it can never invent a delivery.

Every lost compare-and-set logs at DEBUG. Two callers driving one sprint is
the DESIGNED arrangement here rather than an anomaly, so exactly one of them
loses each hop by construction and a WARNING would fire on correct
behaviour, in proportion to how well the recovery sweep is doing its job.
What a lost hop leaves behind is not silent either way: the row is a state
the next pass reads and re-asks about.
"""

from synthorg.core.clock import Clock
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow._sprint_ops import log_sprint_transition
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import SPRINT_TRANSITION_LOST
from synthorg.persistence.sprint_protocol import SprintRepository

logger = get_logger(__name__)


def backlog_fully_delivered(sprint: Sprint) -> bool:
    """Whether every task in *sprint*'s backlog has been completed.

    A sprint whose backlog is empty is not delivered: it has nothing to
    review, and treating "no tasks" as "all tasks done" would end a sprint
    the moment it was created. The counts alone decide it because
    ``Sprint``'s own validator holds ``completed_task_ids`` to a
    duplicate-free subset of ``task_ids``.

    Returns:
        ``True`` when the backlog is non-empty and fully delivered.
    """
    if not sprint.task_ids:
        return False
    return len(sprint.completed_task_ids) >= len(sprint.task_ids)


async def open_review_if_delivered(
    sprint: Sprint, *, sprints: SprintRepository
) -> Sprint:
    """Advance ACTIVE to IN_REVIEW once the whole backlog is delivered.

    Args:
        sprint: The sprint as the caller last read it.
        sprints: The durable store, whose CAS decides the hop.

    Returns:
        The sprint after the transition, or unchanged when it is not
        ACTIVE, the backlog is not yet fully delivered, or the CAS was
        lost. A lost CAS means another caller moved the row, so this one
        returning unchanged is correct rather than a failure.
    """
    if sprint.status is not SprintStatus.ACTIVE:
        return sprint
    if not backlog_fully_delivered(sprint):
        return sprint
    if not await sprints.transition_if(
        NotBlankStr(sprint.id), sprint.status, SprintStatus.IN_REVIEW
    ):
        logger.debug(
            SPRINT_TRANSITION_LOST,
            sprint_id=sprint.id,
            from_status=sprint.status.value,
            to_status=SprintStatus.IN_REVIEW.value,
            note="backlog_delivered",
        )
        return sprint
    transitioned = sprint.with_transition(SprintStatus.IN_REVIEW)
    log_sprint_transition(transitioned, sprint.status)
    return transitioned


async def finalize_if_delivered(
    sprint: Sprint, *, sprints: SprintRepository, clock: Clock
) -> Sprint:
    """Walk IN_REVIEW -> RETROSPECTIVE -> COMPLETED when all tasks are done.

    The two hops are separate compare-and-sets rather than one, because
    RETROSPECTIVE is a real state an operator can act in and the lifecycle
    refuses to skip it. Two callers arriving together are safe without a
    lock: the loser's first CAS finds RETROSPECTIVE instead of IN_REVIEW
    and declines, so only one of them reaches the second hop.

    Args:
        sprint: The sprint as the caller last read it.
        sprints: The durable store, whose CAS decides each hop.
        clock: Supplies the ``end_date`` stamped on completion.

    Returns:
        The completed sprint; *sprint* unchanged when it is not IN_REVIEW,
        the backlog is not delivered, or the first CAS was lost; and the
        RETROSPECTIVE sprint when only the second was lost, since by then
        the row really is there and returning the IN_REVIEW pre-image would
        report a state that no longer exists.
    """
    if sprint.status is not SprintStatus.IN_REVIEW:
        return sprint
    if not backlog_fully_delivered(sprint):
        return sprint
    if not await sprints.transition_if(
        NotBlankStr(sprint.id),
        SprintStatus.IN_REVIEW,
        SprintStatus.RETROSPECTIVE,
    ):
        logger.debug(
            SPRINT_TRANSITION_LOST,
            sprint_id=sprint.id,
            from_status=SprintStatus.IN_REVIEW.value,
            to_status=SprintStatus.RETROSPECTIVE.value,
            note="finalize_review_to_retro",
        )
        return sprint
    # Logged per hop rather than once for the walk: RETROSPECTIVE is a state
    # the row genuinely occupied, and a single line spanning both hops leaves
    # no record that it was ever there.
    retro = sprint.model_copy(update={"status": SprintStatus.RETROSPECTIVE})
    log_sprint_transition(retro, SprintStatus.IN_REVIEW)
    completed = retro.with_transition(
        SprintStatus.COMPLETED, end_date=clock.now().isoformat()
    )
    if not await sprints.transition_if(
        NotBlankStr(sprint.id),
        SprintStatus.RETROSPECTIVE,
        SprintStatus.COMPLETED,
        end_date=completed.end_date,
    ):
        logger.debug(
            SPRINT_TRANSITION_LOST,
            sprint_id=sprint.id,
            from_status=SprintStatus.RETROSPECTIVE.value,
            to_status=SprintStatus.COMPLETED.value,
            note="finalize_retro_to_completed",
        )
        return retro
    log_sprint_transition(completed, SprintStatus.RETROSPECTIVE)
    return completed


async def advance_tail(
    sprint: Sprint, *, sprints: SprintRepository, clock: Clock
) -> Sprint:
    """Walk *sprint* as far along the tail as its delivery allows.

    Idempotent, so a caller may run it against a sprint another caller is
    already advancing.

    Args:
        sprint: The sprint as the caller last read it.
        sprints: The durable store, whose CAS decides each hop.
        clock: Supplies the ``end_date`` stamped on completion.

    Returns:
        The sprint after every hop it could take.
    """
    reviewing = await open_review_if_delivered(sprint, sprints=sprints)
    return await finalize_if_delivered(reviewing, sprints=sprints, clock=clock)


__all__ = [
    "advance_tail",
    "backlog_fully_delivered",
    "finalize_if_delivered",
    "open_review_if_delivered",
]
