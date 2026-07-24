# module-kind: code
"""Read where a plan's tail stages have got to.

The rollup decides what to do at INTEGRATING and EVALUATING; this reads the
state those decisions are made from, so the rollup itself stays a derivation
plus a set of triggers rather than growing a second stage machine.

Integration outcome is read from the integration task's persisted status,
which means it composes with the review gate exactly as everything else here
does: the task is only ``COMPLETED`` once the completion-oracle chain (with its
build/test oracle over the run's execution records) passed it.
"""

from enum import StrEnum

from synthorg.core.task_enums import TaskStatus
from synthorg.persistence.protocol import PersistenceBackend


class IntegrationOutcome(StrEnum):
    """Where the integration job for a plan has got to.

    ``ABSENT``: no integration task exists yet, so the stage has not started.
    ``RUNNING``: one exists and has not reached a terminal status.
    ``PASSED``: it completed, which under a wired runtime means it passed the
    review gate's oracle chain. ``FAILED``: it failed, was rejected by the
    gate, or was cancelled.
    """

    ABSENT = "absent"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


#: Terminal statuses that mean the assembly job did not deliver. REJECTED is
#: included deliberately: a gate rejection is the oracle refusing an unverified
#: or failing integration, which is the signal this stage exists to surface.
_FAILED_STATUSES = frozenset(
    {TaskStatus.FAILED, TaskStatus.REJECTED, TaskStatus.CANCELLED}
)


async def read_integration_outcome(
    persistence: PersistenceBackend,
    *,
    task_id: str,
) -> IntegrationOutcome:
    """Return where the integration job identified by *task_id* has got to.

    Args:
        persistence: Backend supplying the task repository.
        task_id: The deterministic integration-task id for a plan.

    Returns:
        The :class:`IntegrationOutcome` for that plan.
    """
    task = await persistence.tasks.get(task_id)
    if task is None:
        return IntegrationOutcome.ABSENT
    if task.status is TaskStatus.COMPLETED:
        return IntegrationOutcome.PASSED
    if task.status in _FAILED_STATUSES:
        return IntegrationOutcome.FAILED
    return IntegrationOutcome.RUNNING
