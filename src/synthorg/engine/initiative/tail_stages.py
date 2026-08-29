# module-kind: code
"""Read where a plan's assembly stage has got to.

The rollup decides what to do at INTEGRATING; this reads the state that decision
is made from, so the rollup itself stays a derivation plus a set of triggers
rather than growing a second stage machine.

Integration outcome is read from the integration task's persisted status, which
means it composes with the review gate exactly as everything else here does: the
task is only ``COMPLETED`` once the completion-oracle chain (with its build/test
oracle over the run's execution records) passed it.

The walk itself lives in
:mod:`synthorg.engine.initiative.stage_state`, shared with the skeleton stage at
the other end of the run. This module is the binding: the namespace assembly
derives its ids in, the actor its rows carry, and how many attempts it gets.
"""

from typing import Final
from uuid import NAMESPACE_OID, UUID, uuid5

from synthorg.core.plan import Plan
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.stage_state import (
    StageBinding,
    StageState,
    is_stage_task,
    read_stage_state,
    stage_task_id,
    stage_task_uuid,
)
from synthorg.persistence.protocol import PersistenceBackend

#: Namespace integration task ids are derived in, so they are stable across
#: processes and restarts without colliding with any other derived id. Frozen
#: by every id already persisted under it: changing the seed string orphans
#: every assembly row a live deployment holds.
_TASK_NAMESPACE: Final[UUID] = uuid5(NAMESPACE_OID, "synthorg.initiative.integrate")

#: Ceiling on assembly attempts for one plan. Past it the initiative is parked
#: for an operator rather than assembling forever: repeated failures are a
#: planning problem, and the replan trigger has its own generation cap for
#: exactly the same reason.
MAX_INTEGRATION_ATTEMPTS: Final[int] = 3

#: Identity recorded on the integration task, so the board shows the stage
#: rather than attributing the work to whoever last touched the initiative.
INTEGRATION_ACTOR: Final[str] = "initiative-integrate"

#: What tells this stage apart from every other stage that mints a task.
INTEGRATION_BINDING: Final[StageBinding] = StageBinding(
    namespace=_TASK_NAMESPACE,
    actor=NotBlankStr(INTEGRATION_ACTOR),
    max_attempts=MAX_INTEGRATION_ATTEMPTS,
)


def integration_task_uuid(plan: Plan, attempt: int) -> UUID:
    """Return the deterministic id of one assembly attempt for *plan*.

    Returns:
        The attempt's task id.
    """
    return stage_task_uuid(INTEGRATION_BINDING, plan, attempt)


def integration_task_id(plan: Plan, attempt: int) -> str:
    """Return :func:`integration_task_uuid` in the repositories' string form.

    Returns:
        The attempt's task id, as a canonical UUID string.
    """
    return stage_task_id(INTEGRATION_BINDING, plan, attempt)


def is_integration_task(task: Task, plan: Plan) -> bool:
    """Whether *task* is genuinely *plan*'s assembly job.

    Returns:
        ``True`` when the row was minted by this stage for this plan.
    """
    return is_stage_task(task, plan, binding=INTEGRATION_BINDING)


async def read_integration_state(
    persistence: PersistenceBackend,
    plan: Plan,
    *,
    allow_new_attempt: bool,
) -> StageState:
    """Return which assembly attempt *plan* is on and where it got to.

    Args:
        persistence: Backend supplying the task repository.
        plan: The plan whose assembly attempts are being read.
        allow_new_attempt: Whether a spent attempt may be stepped over, which
            the rollup allows exactly when the plan has just re-entered
            INTEGRATING.

    Returns:
        The :class:`~synthorg.engine.initiative.stage_state.StageState` the
        rollup should act on.
    """
    return await read_stage_state(
        persistence,
        plan,
        binding=INTEGRATION_BINDING,
        allow_new_attempt=allow_new_attempt,
    )
