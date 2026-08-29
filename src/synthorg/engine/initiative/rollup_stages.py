# module-kind: code
"""Drive whichever staged job a plan currently sits in.

A stage owns its own verdict: it mints a task keyed on an id derived from the
plan, and the plan advances on that task's persisted status rather than on
anything derived here. So these functions only read where a stage got to and
move the plan accordingly, which is what keeps the rollup a derivation plus a
set of triggers instead of a second stage machine.

They live apart from the rollup for two reasons. The rollup is at its size
budget and this is the part of it that grows every time a stage is added. And
they are pure over their collaborators, which is what lets a test drive one
stage without standing up a rollup.

**An unwired stage parks the plan and says so, on every recompute.** The warning
repeating is the point: an initiative whose contract was never written has not
been built, and one whose pieces were never assembled has not been delivered.
Advancing past either would report progress nobody made.
"""

from collections.abc import Awaitable, Callable

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.engine.initiative.head_stages import read_skeleton_state
from synthorg.engine.initiative.ports import (
    EvaluationPort,
    IntegrationPort,
    SkeletonPort,
)
from synthorg.engine.initiative.stage_state import StageOutcome
from synthorg.engine.initiative.tail_stages import read_integration_state
from synthorg.observability import get_logger
from synthorg.observability.events.project import (
    PROJECT_ROLLUP_SKIPPED,
    PROJECT_ROLLUP_STARTED,
)
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

#: Advances a plan to a status, answering ``None`` when the write was refused.
PlanAdvance = Callable[[Plan, PlanStatus], Awaitable[Plan | None]]

#: Routes a plan whose stage failed to a replan or to a person.
StallRoute = Callable[[Plan], Awaitable[Plan]]

#: Stage outcomes a stage can act on by dispatching: no attempt yet, or one
#: whose row was persisted and then never handed to the pipeline.
_DISPATCHABLE: frozenset[StageOutcome] = frozenset(
    {StageOutcome.ABSENT, StageOutcome.PENDING}
)


def _log_running(plan: Plan, *, note: str) -> None:
    """Record that a stage job is still in flight.

    Logged rather than passed over in silence: a job that is genuinely working
    and one that died without terminalising its row look identical from here,
    and this line is the only place an operator can tell how long the plan has
    been waiting.
    """
    logger.debug(
        PROJECT_ROLLUP_STARTED,
        plan_id=str(plan.id),
        plan_status=plan.status.value,
        note=note,
    )


async def drive_skeleton(
    plan: Plan,
    *,
    persistence: PersistenceBackend,
    skeleton: SkeletonPort | None,
    reopened: bool,
    advance: PlanAdvance,
    stall: StallRoute,
) -> Plan:
    """Fire or read the SKELETON stage for a plan sitting in it.

    *reopened* says whether this recompute is what put the plan into SKELETON.
    Only then may a spent attempt be stepped over: that is the difference
    between rewriting a contract that was found wrong and re-running the same
    failed attempt on every event.

    Returns:
        The plan, advanced to EXECUTING when the contract job passed.
    """
    if skeleton is None:
        logger.warning(
            PROJECT_ROLLUP_SKIPPED,
            plan_id=str(plan.id),
            reason="skeleton_stage_unwired",
            note="plan parked at skeleton; no unit will be dispatched",
        )
        return plan
    state = await read_skeleton_state(persistence, plan, allow_new_attempt=reopened)
    if state.outcome in _DISPATCHABLE:
        skeleton.schedule(plan=plan, attempt=state.attempt)
        return plan
    if state.outcome is StageOutcome.PASSED:
        return await advance(plan, PlanStatus.EXECUTING) or plan
    if state.outcome is StageOutcome.FAILED:
        # A contract that will not compile is a statement about the plan rather
        # than about the agent that wrote it, so this routes to a replan exactly
        # as a failed assembly does. It is the cheapest failure in the run:
        # nothing has been built against the contract yet.
        return await stall(plan)
    if state.outcome is StageOutcome.RUNNING:
        _log_running(plan, note="skeleton job still running")
    return plan


async def drive_integration(
    plan: Plan,
    *,
    persistence: PersistenceBackend,
    integration: IntegrationPort | None,
    reopened: bool,
    advance: PlanAdvance,
    stall: StallRoute,
) -> Plan:
    """Fire or read the INTEGRATE stage for a plan sitting in it.

    Returns:
        The plan, advanced to EVALUATING when the assembly job passed.
    """
    if integration is None:
        logger.warning(
            PROJECT_ROLLUP_SKIPPED,
            plan_id=str(plan.id),
            reason="integration_stage_unwired",
            note="plan parked at integrating; it will not auto-complete",
        )
        return plan
    state = await read_integration_state(persistence, plan, allow_new_attempt=reopened)
    if state.outcome in _DISPATCHABLE:
        integration.schedule(plan=plan, attempt=state.attempt)
        return plan
    if state.outcome is StageOutcome.PASSED:
        return await advance(plan, PlanStatus.EVALUATING) or plan
    if state.outcome is StageOutcome.FAILED:
        # The pieces work and the whole does not, which no derivation over items
        # can see: every item is COMPLETED here. Asked rather than assumed, so a
        # refusal reaches the operator instead of being rescheduled on every
        # recompute for the life of the process.
        return await stall(plan)
    if state.outcome is StageOutcome.RUNNING:
        _log_running(plan, note="integration job still running")
    return plan


def drive_evaluation(plan: Plan, *, evaluation: EvaluationPort | None) -> None:
    """Fire the EVALUATE stage, or park the plan visibly.

    The stage owns the only transition that can complete a plan, so this never
    advances anything itself: it either hands the plan to the judgement or says
    loudly that no judgement can happen.
    """
    if evaluation is None:
        logger.warning(
            PROJECT_ROLLUP_SKIPPED,
            plan_id=str(plan.id),
            reason="evaluation_stage_unwired",
            note="plan parked at evaluating; it will not auto-complete",
        )
        return
    evaluation.schedule(plan=plan)
