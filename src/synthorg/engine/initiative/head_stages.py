# module-kind: code
"""Read where a plan's skeleton stage has got to.

The rollup decides what to do at SKELETON; this reads the state that decision is
made from, so the rollup stays a derivation plus a set of triggers rather than
growing a second stage machine.

Skeleton outcome is read from the skeleton task's persisted status, which means
it composes with the review gate exactly as the assembly stage does: the task is
only ``COMPLETED`` once the completion-oracle chain passed it. That is what makes
the contract *reviewed* rather than merely written, which matters more here than
anywhere else in the run, because every unit below is briefed from it and a
contract nobody read is one every leaf inherits.

The walk itself lives in :mod:`synthorg.engine.initiative.stage_state`, shared
with the assembly stage at the other end of the run. This module is the binding:
the namespace the skeleton derives its ids in, the actor its rows carry, and how
many attempts it gets.
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

#: Namespace skeleton task ids are derived in. Distinct from the assembly
#: stage's, so one plan's two stage tasks can never collide on an id even though
#: both are derived from the same plan id and attempt index.
_TASK_NAMESPACE: Final[UUID] = uuid5(NAMESPACE_OID, "synthorg.initiative.skeleton")

#: Attempts a plan gets at its own contract, which is ONE.
#:
#: A failed contract is a statement about the plan rather than about the agent
#: that tried, so the answer is a replan, and a replan is a NEW plan with its
#: own first attempt. There is no route back into SKELETON for the plan that
#: failed: the transition table declares none, and ``derive_plan_status``
#: returns a head status unchanged, so nothing can re-enter the stage the way
#: the tail re-enters INTEGRATING through EXECUTING.
#:
#: Written as one because it IS one. A larger number here was pure decoration:
#: stepping over a spent attempt needs the rollup to observe the plan
#: re-entering the stage in the same pass, which cannot happen at SKELETON, so
#: attempts one and two were unreachable and the ceiling described behaviour
#: nothing could produce.
MAX_SKELETON_ATTEMPTS: Final[int] = 1

#: Identity recorded on the skeleton task, so the board shows the stage rather
#: than attributing the contract to whoever last touched the initiative.
SKELETON_ACTOR: Final[str] = "initiative-skeleton"

#: What tells this stage apart from every other stage that mints a task. The
#: actor is the load-bearing field: a skeleton row and an assembly row are alike
#: in carrying the plan id and no item id, so provenance turns on this alone.
SKELETON_BINDING: Final[StageBinding] = StageBinding(
    namespace=_TASK_NAMESPACE,
    actor=NotBlankStr(SKELETON_ACTOR),
    max_attempts=MAX_SKELETON_ATTEMPTS,
)


def skeleton_task_uuid(plan: Plan, attempt: int) -> UUID:
    """Return the deterministic id of one skeleton attempt for *plan*.

    Returns:
        The attempt's task id.
    """
    return stage_task_uuid(SKELETON_BINDING, plan, attempt)


def skeleton_task_id(plan: Plan, attempt: int) -> str:
    """Return :func:`skeleton_task_uuid` in the repositories' string form.

    Returns:
        The attempt's task id, as a canonical UUID string.
    """
    return stage_task_id(SKELETON_BINDING, plan, attempt)


def is_skeleton_task(task: Task, plan: Plan) -> bool:
    """Whether *task* is genuinely *plan*'s skeleton job.

    Returns:
        ``True`` when the row was minted by this stage for this plan.
    """
    return is_stage_task(task, plan, binding=SKELETON_BINDING)


async def read_skeleton_state(
    persistence: PersistenceBackend,
    plan: Plan,
    *,
    allow_new_attempt: bool,
) -> StageState:
    """Return which skeleton attempt *plan* is on and where it got to.

    Args:
        persistence: Backend supplying the task repository.
        plan: The plan whose skeleton attempts are being read.
        allow_new_attempt: Whether a spent attempt may be stepped over, which
            the rollup allows exactly when the plan has just re-entered
            SKELETON.

    Returns:
        The :class:`~synthorg.engine.initiative.stage_state.StageState` the
        rollup should act on.
    """
    return await read_stage_state(
        persistence,
        plan,
        binding=SKELETON_BINDING,
        allow_new_attempt=allow_new_attempt,
    )
