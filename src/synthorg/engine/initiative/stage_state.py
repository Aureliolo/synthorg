# module-kind: code
"""Where one plan's staged job has got to, for every stage that mints one.

A stage here is a lifecycle step that does its work as an ordinary task rather
than as bespoke machinery: it derives a task id from the plan, mints the row if
it is absent, and reads the row's own persisted status back as its verdict. That
shape is what makes a stage inherit the whole verification chain (the build/test
oracle over the run's execution records, then the completion-oracle peer review)
with no second oracle written anywhere, and it is why a stage needs no
"already started" flag: the derived id **is** the idempotency mechanism, so
there is nothing to keep in step with reality.

Two stages use it and they sit at opposite ends of the run. The skeleton writes
the contract before any unit builds against it; the assembly proves the verified
pieces work together at the end. They differ only in their namespace, their
actor, and how many attempts they get, so those three are the binding and
everything else is here once. Written twice instead, the copies would drift on
exactly the details that are easy to get wrong and invisible when wrong: which
statuses are re-dispatchable, and whether an unrecognised row reads as a pass.

An initiative can need more than one attempt at either stage, so attempts are
numbered and a spent one is stepped over only when the plan has just re-entered
the stage. Numbering rather than reusing a single id is what keeps the rework
edge reachable instead of leaving a permanently failed row the stage re-reads.
"""

from enum import StrEnum
from typing import Final
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.plan import Plan
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend


class StageOutcome(StrEnum):
    """Where a staged job for one plan has got to.

    ``ABSENT``: no task exists for this attempt, so it has not started.
    ``PENDING``: a row exists but nothing is driving it, so the dispatch died
    between persisting it and handing it to the pipeline, or the process that
    held it stopped; it is re-dispatchable rather than lost. ``RUNNING``: one
    exists and is under way. ``PASSED``: it completed, which under a wired
    runtime means it passed the review gate's oracle chain. ``FAILED``: it
    failed, was rejected by the gate, or was cancelled, and no further attempt
    is available.
    """

    ABSENT = "absent"
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


#: Terminal statuses that mean an attempt did not deliver. REJECTED is included
#: deliberately: a gate rejection is the oracle refusing unverified or failing
#: work, which is the signal a stage exists to surface.
_FAILED_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.FAILED, TaskStatus.REJECTED, TaskStatus.CANCELLED}
)

#: Statuses in which an attempt exists but nothing is driving it, so the stage
#: hands it out again rather than waiting. ``CREATED`` means the dispatch died
#: between persisting the row and handing it to the pipeline; ``INTERRUPTED``
#: means it reached the pipeline and the process running it stopped, which run
#: recovery records on the row. Neither is a verdict, and reading either as
#: RUNNING parks the initiative for ever.
_REDISPATCHABLE_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.CREATED, TaskStatus.INTERRUPTED}
)


class StageBinding(BaseModel):
    """Everything one stage needs that another stage answers differently.

    Attributes:
        namespace: UUID namespace this stage derives its task ids in, so two
            stages never mint the same id for one plan.
        actor: Identity recorded on the stage's tasks. It is the third thing
            provenance checks, and the only one a stage cannot share: the plan
            id and the absent item id are true of every stage's row.
        max_attempts: Ceiling on attempts for one plan. Past it the initiative
            is parked for an operator rather than retrying forever, because
            repeated failure at a stage is a planning problem.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    namespace: UUID
    actor: NotBlankStr
    max_attempts: int = Field(ge=1)


class StageState(BaseModel):
    """Which attempt a plan is on at one stage, and where that attempt got to.

    Attributes:
        attempt: Zero-based index of the attempt the outcome describes.
        outcome: Where that attempt has got to.
        task_id: The attempt's deterministic task id, so a caller acting on the
            outcome addresses the same row this was read from.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    attempt: int = Field(ge=0, description="Zero-based attempt index")
    outcome: StageOutcome = Field(description="Where that attempt got to")
    task_id: UUID = Field(description="Deterministic task id for the attempt")


def stage_task_uuid(binding: StageBinding, plan: Plan, attempt: int) -> UUID:
    """Return the deterministic id of one attempt at *binding*'s stage.

    Derived rather than random, so a re-fired stage edge resolves the same row
    instead of minting a second job.

    Returns:
        The attempt's task id.
    """
    return uuid5(binding.namespace, f"{plan.id}:{attempt}")


def stage_task_id(binding: StageBinding, plan: Plan, attempt: int) -> str:
    """Return :func:`stage_task_uuid` in the repositories' string form.

    Returns:
        The attempt's task id, as a canonical UUID string.
    """
    return str(stage_task_uuid(binding, plan, attempt))


def is_stage_task(task: Task, plan: Plan, *, binding: StageBinding) -> bool:
    """Whether *task* is genuinely *plan*'s job for *binding*'s stage.

    The id is derivable from the plan id, and plan item ids are caller-supplied,
    so an item could be filed under an attempt's id and impersonate the stage:
    the outcome read would see that item complete and skip the stage entirely.
    Provenance is checked rather than assumed, using the three things only the
    stage sets, of which the actor is the one that also tells two stages apart.

    Returns:
        ``True`` when the row was minted by this stage for this plan.
    """
    return (
        task.plan_id == plan.id
        and task.plan_item_id is None
        and str(task.created_by) == binding.actor
    )


async def read_stage_state(
    persistence: PersistenceBackend,
    plan: Plan,
    *,
    binding: StageBinding,
    allow_new_attempt: bool,
) -> StageState:
    """Return which attempt *plan* is on at *binding*'s stage, and its outcome.

    Walks the attempt sequence from zero. A spent attempt is stepped over only
    when *allow_new_attempt* is set, which the rollup does exactly when the plan
    has just re-entered the stage: without that guard a failed attempt would
    immediately mint its own successor and the stage would never report the
    failure a replan needs.

    Args:
        persistence: Backend supplying the task repository.
        plan: The plan whose attempts are being read.
        binding: The stage being read.
        allow_new_attempt: Whether a spent attempt may be stepped over.

    Returns:
        The :class:`StageState` the rollup should act on.
    """
    for attempt in range(binding.max_attempts):
        task_id = stage_task_uuid(binding, plan, attempt)
        task = await persistence.tasks.get(str(task_id))
        if task is None:
            return StageState(
                attempt=attempt, outcome=StageOutcome.ABSENT, task_id=task_id
            )
        if not is_stage_task(task, plan, binding=binding):
            # Something else occupies this id. It is emphatically not evidence
            # that the stage's work was done, so it reads as a failed attempt:
            # the initiative surfaces for a replan rather than skipping the
            # stage on a row the stage never minted.
            return StageState(
                attempt=attempt, outcome=StageOutcome.FAILED, task_id=task_id
            )
        if task.status is TaskStatus.COMPLETED:
            return StageState(
                attempt=attempt, outcome=StageOutcome.PASSED, task_id=task_id
            )
        if task.status in _FAILED_STATUSES:
            if allow_new_attempt:
                continue
            return StageState(
                attempt=attempt, outcome=StageOutcome.FAILED, task_id=task_id
            )
        if task.status in _REDISPATCHABLE_STATUSES:
            return StageState(
                attempt=attempt, outcome=StageOutcome.PENDING, task_id=task_id
            )
        return StageState(
            attempt=attempt, outcome=StageOutcome.RUNNING, task_id=task_id
        )
    last = binding.max_attempts - 1
    return StageState(
        attempt=last,
        outcome=StageOutcome.FAILED,
        task_id=stage_task_uuid(binding, plan, last),
    )
