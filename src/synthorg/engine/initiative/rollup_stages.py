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
from typing import Final

from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.initiative_extension import EXTENSION_ESCALATION_ACTOR
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_tree import PlanTree
from synthorg.engine.initiative.completion import ItemProgress
from synthorg.engine.initiative.extension_escalation import (
    ExtensionEscalationService,
    decision_for,
)
from synthorg.engine.initiative.extension_state import (
    EXTENSION_IN_PROGRESS_DISPOSITIONS,
    EXTENSION_REFUSED_DISPOSITIONS,
    ExtensionDisposition,
    workstream_needs_extension,
)
from synthorg.engine.initiative.head_stages import read_skeleton_state
from synthorg.engine.initiative.ports import (
    DriveOutcome,
    EvaluationPort,
    IntegrationPort,
    PlanDriver,
    ReplanTriggerPort,
    SkeletonPort,
)
from synthorg.engine.initiative.stage_state import StageOutcome
from synthorg.engine.initiative.tail_stages import read_integration_state
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_EXTENSION_ALREADY_DECIDED,
    INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
)
from synthorg.observability.events.project import (
    PROJECT_ROLLUP_SKIPPED,
    PROJECT_ROLLUP_STARTED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

#: Fallback when no resolver is wired or the read fails: off, since this
#: mechanism is unvalidated by any live round, unlike recursion itself.
_DEFAULT_JIT_EXTENSION_PLANNING_ENABLED: Final[bool] = False

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


async def _dispatch_units(
    plan: Plan,
    *,
    drive: PlanDriver | None,
    stall: StallRoute,
) -> Plan:
    """Hand a plan whose contract just passed to the coordinator.

    An unwired driver is answered before the driver is asked at all, and the
    driver's own three outcomes are deliberately not collapsed either. An
    unwired driver and a driver that already holds the plan are both
    recoverable and neither is the plan's fault: the recovery sweep classifies
    EXECUTING as driven and re-asks on its cadence, so the cost is a delay
    somebody can see. A refusal is not
    recoverable, and leaving it as a delay is how a plan sits at EXECUTING with
    nothing running while a sweep reports rescuing it every pass for ever. So a
    refusal routes to a replan or to a person, which is what a dispatch failure
    did before the contract stage existed.

    Returns:
        The plan, as the stall route left it on a refusal, else unchanged.
    """
    if drive is None:
        logger.warning(
            PROJECT_ROLLUP_SKIPPED,
            plan_id=str(plan.id),
            reason="plan_driver_unwired",
            note="contract passed; waiting on a recovery sweep to dispatch",
        )
        return plan
    outcome = await drive(plan)
    if outcome is DriveOutcome.REFUSED:
        return await stall(plan)
    if outcome is DriveOutcome.HELD:
        logger.debug(
            PROJECT_ROLLUP_SKIPPED,
            plan_id=str(plan.id),
            reason="plan_already_driven",
            note="contract passed; another driver already owns the plan",
        )
    return plan


async def drive_skeleton(
    plan: Plan,
    *,
    persistence: PersistenceBackend,
    skeleton: SkeletonPort | None,
    reopened: bool,
    advance: PlanAdvance,
    stall: StallRoute,
    drive: PlanDriver | None,
) -> Plan:
    """Fire or read the SKELETON stage for a plan sitting in it.

    *reopened* says whether this recompute is what put the plan into SKELETON.
    Only then may a spent attempt be stepped over: that is the difference
    between rewriting a contract that was found wrong and re-running the same
    failed attempt on every event.

    A passing contract is also the moment the plan's units become dispatchable
    for the first time, so *drive* is called on that edge and nowhere else. It
    is the same port the recovery sweep uses, because a plan being driven is a
    plan being driven; what differs is only when the question is asked. Without
    it the plan would reach EXECUTING with nothing running and wait for a
    recovery sweep to notice, which is minutes of silence on the ordinary path.

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
        advanced = await advance(plan, PlanStatus.EXECUTING)
        if advanced is None:
            # A refused transition or an exhausted retry budget, and the plan is
            # still at SKELETON. Falling back to the input here would dispatch
            # the units against a status that never moved, which is the one
            # thing the stage exists to prevent; the next pass re-asks.
            return plan
        return await _dispatch_units(advanced, drive=drive, stall=stall)
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


async def resolve_jit_extension_planning_enabled(
    resolver: ConfigResolver | None,
) -> bool:
    """Return whether the just-in-time extension mechanism runs at all.

    The master switch, read live per recompute so an operator's change
    applies without a restart, on the same shape ``resolve_recursion_budget``
    reads ``coordination.recursive_decomposition_enabled``.

    Returns:
        The live ``coordination.jit_extension_planning_enabled`` value, or
        the default when no resolver is wired or the read fails.
    """
    if resolver is None:
        return _DEFAULT_JIT_EXTENSION_PLANNING_ENABLED
    try:
        return await resolver.get_bool("coordination", "jit_extension_planning_enabled")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read
        reraise_critical(exc)
        logger.warning(
            INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
            key="jit_extension_planning_enabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _DEFAULT_JIT_EXTENSION_PLANNING_ENABLED


async def extensions_hold(
    plan: Plan,
    items: tuple[ItemProgress, ...],
    *,
    config_resolver: ConfigResolver | None,
    replan_trigger: ReplanTriggerPort | None,
    drive: PlanDriver | None,
    extension_escalation: ExtensionEscalationService | None,
) -> bool:
    """Whether a workstream mid-extension should hold *plan* at EXECUTING.

    Off by default and read live, on the same shape as recursion's own master
    switch: an operator turns the mechanism on, and this decides whether it
    is even worth asking every workstream on this pass.

    Returns:
        ``False`` when the switch is off, the trigger is unwired, or no
        workstream needs an extension right now; otherwise whatever
        :func:`drive_extensions` answers.
    """
    if not await resolve_jit_extension_planning_enabled(config_resolver):
        return False
    return await drive_extensions(
        plan,
        items,
        replan_trigger=replan_trigger,
        drive=drive,
        extension_escalation=extension_escalation,
    )


async def drive_extensions(
    plan: Plan,
    items: tuple[ItemProgress, ...],
    *,
    replan_trigger: ReplanTriggerPort | None,
    drive: PlanDriver | None,
    extension_escalation: ExtensionEscalationService | None,
) -> bool:
    """Ask every workstream whether it needs another extension, and act on it.

    Meaningful only while ``plan.status is EXECUTING``; the caller gates on
    that and on the master switch. Called BEFORE ``derive_plan_status`` on
    the same recompute pass, because that derivation promotes a plan to
    INTEGRATING the moment every currently-known item reads done, with no
    workstream-level distinction: a workstream whose extension is in flight
    (or was just started) is not finished even though its known tree is.

    ``ASKED`` is handled here rather than folded into
    ``EXTENSION_IN_PROGRESS_DISPOSITIONS``, because whether it holds the plan
    depends on whether anything can actually ask: with an escalation
    attached, a fresh ask is parked and holds, same as the stall route; an
    already-parked or already-rejected leaf is read straight from the store
    (checked BEFORE re-asking the trigger, so a settled rejection never mints
    a second decision). Without one, on the same reasoning
    ``escalate_stall``'s escalation-absent branch drives a plan out rather
    than park it, the plan is not held: the work already delivered is real,
    and an unmet objective still surfaces at the judged EVALUATING gate.

    An ``APPROVED`` decision is settled, not merely in progress: unlike a
    stall's grant, which supersedes the plan and so ends this loop for it, an
    extension's plan stays EXECUTING and this same leaf recurs on every later
    pass until it either gains children (dropping it from
    ``workstream_needs_extension``'s own answer) or is refused outright. So
    an approval is re-applied here on every pass it is still seen, through
    the same granted door a fresh human decision uses: ``ALREADY_RUNNING``
    covers the ordinary case where the first grant's graft has not finished
    yet, and a settled approval that keeps failing to graft still holds the
    plan rather than silently promoting past an objective nothing delivered.

    Returns:
        Whether at least one workstream is mid-extension, was just handed
        one, has a decision open, or has a settled approval still being
        applied, which the caller reads as "hold this plan at EXECUTING this
        pass". ``False`` for a workstream an extension was asked for and
        refused outright (the switch is off, its generation cap is spent, or
        a settled rejection already answered it): no automatic route remains
        for it, so holding the plan for ever would replace one silent state
        with another.

    Raises:
        AssertionError: If a leaf's disposition is none of ``ASKED``,
            ``EXTENSION_IN_PROGRESS_DISPOSITIONS`` or
            ``EXTENSION_REFUSED_DISPOSITIONS``, meaning a new
            ``ExtensionDisposition`` member was added without updating this
            loop to handle it.
    """
    if replan_trigger is None:
        return False
    tree = PlanTree.of(plan.items)
    progress_by_id = dict(zip((item.id for item in plan.items), items, strict=True))
    decisions = (
        await extension_escalation.open_decisions(plan)
        if extension_escalation is not None
        else ()
    )
    holding = False
    for workstream in tree.workstreams:
        for leaf in workstream_needs_extension(
            plan.items, tree, workstream, progress_by_id
        ):
            if await _drive_leaf_extension(
                plan,
                tree,
                workstream,
                leaf,
                decisions=decisions,
                replan_trigger=replan_trigger,
                drive=drive,
                extension_escalation=extension_escalation,
            ):
                holding = True
    return holding


async def _drive_leaf_extension(
    plan: Plan,
    tree: PlanTree,
    workstream: PlanItem,
    leaf: PlanItem,
    *,
    decisions: tuple[ApprovalItem, ...],
    replan_trigger: ReplanTriggerPort,
    drive: PlanDriver | None,
    extension_escalation: ExtensionEscalationService | None,
) -> bool:
    """Resolve one oversized leaf's extension state for this pass.

    Split out of :func:`drive_extensions` so the per-workstream loop there
    stays a dispatch table; this is the one leaf's worth of decision logic
    it dispatches to.

    Returns:
        Whether this leaf is currently holding the plan at EXECUTING.

    Raises:
        AssertionError: If the disposition is none of ``ASKED``,
            ``EXTENSION_IN_PROGRESS_DISPOSITIONS`` or
            ``EXTENSION_REFUSED_DISPOSITIONS``, meaning a new
            ``ExtensionDisposition`` member was added without updating this
            to handle it.
    """
    decision = decision_for(decisions, leaf)
    status = decision.status if decision is not None else None
    if status is ApprovalStatus.REJECTED:
        logger.debug(
            INITIATIVE_EXTENSION_ALREADY_DECIDED,
            plan_id=str(plan.id),
            leaf_id=leaf.id,
            status=ApprovalStatus.REJECTED.value,
        )
        return False
    if status is ApprovalStatus.PENDING:
        logger.debug(
            INITIATIVE_EXTENSION_ALREADY_DECIDED,
            plan_id=str(plan.id),
            leaf_id=leaf.id,
            status=ApprovalStatus.PENDING.value,
        )
        return True
    if status is ApprovalStatus.APPROVED:
        await replan_trigger.grant_extension(
            plan=plan,
            workstream=workstream,
            leaf=leaf,
            drive=drive,
            requested_by=EXTENSION_ESCALATION_ACTOR,
        )
        return True
    disposition = await replan_trigger.consider_extension(
        plan=plan,
        tree=tree,
        workstream=workstream,
        leaf=leaf,
        drive=drive,
    )
    if disposition is ExtensionDisposition.ASKED:
        if extension_escalation is None:
            return False
        await extension_escalation.escalate(plan, workstream, leaf)
        return True
    if disposition in EXTENSION_IN_PROGRESS_DISPOSITIONS:
        return True
    if disposition not in EXTENSION_REFUSED_DISPOSITIONS:
        # ASKED, EXTENSION_IN_PROGRESS_DISPOSITIONS and
        # EXTENSION_REFUSED_DISPOSITIONS partition every disposition
        # consider_extension can answer; a member reaching here is a new one
        # this was never updated to handle.
        msg = f"unhandled ExtensionDisposition: {disposition!r}"
        raise AssertionError(msg)
    return False


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
