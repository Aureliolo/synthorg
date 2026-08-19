# module-kind: code
"""Read where a plan's tail stages have got to.

The rollup decides what to do at INTEGRATING and EVALUATING; this reads the
state those decisions are made from, so the rollup itself stays a derivation
plus a set of triggers rather than growing a second stage machine.

Integration outcome is read from the integration task's persisted status,
which means it composes with the review gate exactly as everything else here
does: the task is only ``COMPLETED`` once the completion-oracle chain (with its
build/test oracle over the run's execution records) passed it.

An initiative can need more than one assembly attempt: a failed integration can
be reworked, and the plan re-enters INTEGRATING with the same id. Attempts are
therefore numbered, and a spent one is stepped over only when the plan has just
re-entered the stage. Numbering them rather than reusing a single id is what
makes the ``INTEGRATING -> EXECUTING -> INTEGRATING`` rework edge reachable
instead of a permanently failed row the stage keeps re-reading.
"""

from enum import StrEnum
from typing import Final
from uuid import NAMESPACE_OID, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.plan import Plan
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.persistence.protocol import PersistenceBackend

#: Namespace integration task ids are derived in, so they are stable across
#: processes and restarts without colliding with any other derived id.
_TASK_NAMESPACE: Final[UUID] = uuid5(NAMESPACE_OID, "synthorg.initiative.integrate")

#: Ceiling on assembly attempts for one plan. Past it the initiative is parked
#: for an operator rather than assembling forever: repeated failures are a
#: planning problem, and the replan trigger has its own generation cap for
#: exactly the same reason.
MAX_INTEGRATION_ATTEMPTS: Final[int] = 3

#: Identity recorded on the integration task, so the board shows the stage
#: rather than attributing the work to whoever last touched the initiative.
INTEGRATION_ACTOR: Final[str] = "initiative-integrate"


class IntegrationOutcome(StrEnum):
    """Where the integration job for a plan has got to.

    ``ABSENT``: no task exists for this attempt, so it has not started.
    ``PENDING``: a row exists but never left ``CREATED``, so the dispatch died
    between persisting it and handing it to the pipeline; it is re-dispatchable
    rather than lost. ``RUNNING``: one exists and is under way. ``PASSED``: it
    completed, which under a wired runtime means it passed the review gate's
    oracle chain. ``FAILED``: it failed, was rejected by the gate, or was
    cancelled, and no further attempt is available.
    """

    ABSENT = "absent"
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


#: Terminal statuses that mean an assembly attempt did not deliver. REJECTED is
#: included deliberately: a gate rejection is the oracle refusing an unverified
#: or failing integration, which is the signal this stage exists to surface.
_FAILED_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.FAILED, TaskStatus.REJECTED, TaskStatus.CANCELLED}
)

#: Statuses in which an assembly attempt exists but nothing is driving it, so
#: the stage should hand it out again rather than wait. ``CREATED`` means the
#: dispatch died between persisting the row and handing it to the pipeline;
#: ``INTERRUPTED`` means it reached the pipeline and the process running it
#: stopped, which run recovery records on the row. Neither is a verdict, and
#: reading either as RUNNING parks the initiative for ever.
_REDISPATCHABLE_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.CREATED, TaskStatus.INTERRUPTED}
)


class IntegrationState(BaseModel):
    """Which assembly attempt a plan is on, and where that attempt got to.

    Attributes:
        attempt: Zero-based index of the attempt the outcome describes.
        outcome: Where that attempt has got to.
        task_id: The attempt's deterministic task id, so a caller acting on the
            outcome addresses the same row this was read from.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    attempt: int = Field(ge=0, description="Zero-based assembly attempt index")
    outcome: IntegrationOutcome = Field(description="Where that attempt got to")
    task_id: UUID = Field(description="Deterministic task id for the attempt")


def integration_task_uuid(plan: Plan, attempt: int) -> UUID:
    """Return the deterministic id of one assembly attempt for *plan*.

    Derived rather than random, so a re-fired stage edge resolves the same row
    instead of minting a second assembly job. That is the whole idempotency
    mechanism: there is no separate "already started" flag to keep in step
    with reality.

    Returns:
        The attempt's task id.
    """
    return uuid5(_TASK_NAMESPACE, f"{plan.id}:{attempt}")


def integration_task_id(plan: Plan, attempt: int) -> str:
    """Return :func:`integration_task_uuid` in the repositories' string form.

    Returns:
        The attempt's task id, as a canonical UUID string.
    """
    return str(integration_task_uuid(plan, attempt))


def is_integration_task(task: Task, plan: Plan) -> bool:
    """Whether *task* is genuinely *plan*'s assembly job.

    The id is derivable from the plan id, and plan item ids are caller-supplied,
    so an item could be filed under an attempt's id and impersonate the stage:
    the outcome read would see that item complete and skip assembly, the end-to-
    end run, and the escalated review gate entirely. Provenance is checked
    rather than assumed, using the three things only the stage sets.

    Returns:
        ``True`` when the row was minted by this stage for this plan.
    """
    return (
        task.plan_id == plan.id
        and task.plan_item_id is None
        and str(task.created_by) == INTEGRATION_ACTOR
    )


async def read_integration_state(
    persistence: PersistenceBackend,
    plan: Plan,
    *,
    allow_new_attempt: bool,
) -> IntegrationState:
    """Return which assembly attempt *plan* is on and where it got to.

    Walks the attempt sequence from zero. A spent attempt is stepped over only
    when *allow_new_attempt* is set, which the rollup does exactly when the
    plan has just re-entered INTEGRATING: without that guard a failed assembly
    would immediately mint its own successor and the stage would never report
    the failure the replan trigger needs.

    Args:
        persistence: Backend supplying the task repository.
        plan: The plan whose assembly attempts are being read.
        allow_new_attempt: Whether a spent attempt may be stepped over.

    Returns:
        The :class:`IntegrationState` the rollup should act on.
    """
    for attempt in range(MAX_INTEGRATION_ATTEMPTS):
        task_id = integration_task_uuid(plan, attempt)
        task = await persistence.tasks.get(str(task_id))
        if task is None:
            return IntegrationState(
                attempt=attempt,
                outcome=IntegrationOutcome.ABSENT,
                task_id=task_id,
            )
        if not is_integration_task(task, plan):
            # Something else occupies this id. It is emphatically not evidence
            # that the whole was assembled, so it reads as a failed attempt:
            # the initiative surfaces for a replan rather than skipping the
            # stage on a row the stage never minted.
            return IntegrationState(
                attempt=attempt,
                outcome=IntegrationOutcome.FAILED,
                task_id=task_id,
            )
        if task.status is TaskStatus.COMPLETED:
            return IntegrationState(
                attempt=attempt,
                outcome=IntegrationOutcome.PASSED,
                task_id=task_id,
            )
        if task.status in _FAILED_STATUSES:
            if allow_new_attempt:
                continue
            return IntegrationState(
                attempt=attempt,
                outcome=IntegrationOutcome.FAILED,
                task_id=task_id,
            )
        if task.status in _REDISPATCHABLE_STATUSES:
            # Never handed to the pipeline, or handed to one that stopped
            # before reaching a verdict. Either way the row is
            # re-dispatchable, and reporting it as RUNNING would park the
            # initiative on a row nothing is driving.
            return IntegrationState(
                attempt=attempt,
                outcome=IntegrationOutcome.PENDING,
                task_id=task_id,
            )
        return IntegrationState(
            attempt=attempt,
            outcome=IntegrationOutcome.RUNNING,
            task_id=task_id,
        )
    return IntegrationState(
        attempt=MAX_INTEGRATION_ATTEMPTS - 1,
        outcome=IntegrationOutcome.FAILED,
        task_id=integration_task_uuid(plan, MAX_INTEGRATION_ATTEMPTS - 1),
    )
