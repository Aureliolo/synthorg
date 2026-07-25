# module-kind: service
"""Initiative rollup: advance a plan and its project from their own work.

Registered as a :class:`TaskEngine` observer, so it sees every task status
write regardless of which path produced it (the review gate's decision, the
execution loop's failure handling, an operator cancellation).

Two properties make this correct, and both are deliberate:

**It recomputes, it does not accumulate.** The event is only a trigger. On each
one the service re-queries every task for the plan and derives the plan and
project status from scratch. Observers are explicitly best-effort (bounded
queue, drained at shutdown), so events can be dropped or redelivered; a full
recompute is idempotent, which means the next event repairs any drift and a
duplicate event changes nothing. An incremental counter would corrupt
permanently on a single dropped event.

**It reads persisted task status, never execution outcomes.** Under the wired
agent runtime a task reaches ``COMPLETED`` through the review gate, which runs
the completion-oracle chain, so deriving from persisted status composes with
the verify gate without this service calling an oracle: an initiative does not
complete on work that merely executed. Every other writer that could reach
``COMPLETED`` without the gate is fenced, so this holds structurally rather
than by which services happen to be wired.

What it derives is the *tail*, never delivery. All items done means the work is
ready to be assembled, so the plan moves to INTEGRATING and the tail's own
gates decide from there; nothing in this service can write ``COMPLETED`` onto a
plan.
"""

from typing import Final
from uuid import UUID

from synthorg.core.clock import Clock
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError, VersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import TERMINAL_STATUSES, PlanItemKind, PlanStatus
from synthorg.core.plan_transitions import transition_path
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.parent_rollup import (
    advance_parent_to_rollup_status,
)
from synthorg.engine.decomposition.rollup import StatusRollup
from synthorg.engine.initiative.completion import (
    ItemProgress,
    StallReason,
    derive_plan_status,
    derive_project_status,
    stall_reason,
)
from synthorg.engine.initiative.item_progress import collect_item_progress
from synthorg.engine.initiative.ports import (
    EvaluationFactory,
    EvaluationPort,
    IntegrationPort,
    PlanStatusWriter,
    ReplanTriggerPort,
    RetroCapturePort,
)
from synthorg.engine.initiative.project_writes import (
    MAX_WRITE_ATTEMPTS,
    advance_project_status,
)
from synthorg.engine.initiative.tail_stages import (
    IntegrationOutcome,
    read_integration_state,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.project import (
    PROJECT_ROLLUP_COMPLETED,
    PROJECT_ROLLUP_CONFLICT_EXHAUSTED,
    PROJECT_ROLLUP_CONFLICT_RETRY,
    PROJECT_ROLLUP_FAILED,
    PROJECT_ROLLUP_SKIPPED,
    PROJECT_ROLLUP_STARTED,
)
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

#: Identity recorded on rollup-driven status writes, so the audit log
#: distinguishes a derived transition from an operator decision.
_ACTOR: Final[str] = "initiative-rollup"

#: Integration outcomes the stage can act on by dispatching: no attempt yet, or
#: one whose row was persisted and then never handed to the pipeline.
_DISPATCHABLE_OUTCOMES: Final[frozenset[IntegrationOutcome]] = frozenset(
    {IntegrationOutcome.ABSENT, IntegrationOutcome.PENDING}
)

#: Statuses that read as "the objective is over" on the board. The objective
#: outlives every individual item, so the parent walk may only land one of
#: these once the plan itself has delivered.
_OBJECTIVE_FINISHED_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
    }
)


def _parent_status_of(item: ItemProgress) -> TaskStatus:
    """Project one plan item onto the task status the parent rolls up.

    A ``DECISION`` item dispatches no task, so it contributes the status its
    resolution implies: ``COMPLETED`` once an option is recorded, and
    ``IN_PROGRESS`` while the choice is still open (it is real work the
    operator owes, so it must hold the parent open). A ``WORK`` item with no
    dispatched task yet is likewise still pending.

    Returns:
        The ``TaskStatus`` this item contributes to the parent rollup.
    """
    if item.kind is PlanItemKind.DECISION:
        return (
            TaskStatus.COMPLETED
            if item.chosen_option_id is not None
            else TaskStatus.IN_PROGRESS
        )
    return item.task_status if item.task_status is not None else TaskStatus.IN_PROGRESS


class ProjectRollupService:
    """Keep a plan and its project in step with the tasks implementing it.

    Args:
        persistence: Backend supplying the plan, task, and project repositories.
        plan_status_writer: The audited plan-status write path (injected so the
            engine does not import the api service layer).
        clock: Clock seam, retained for the service lifecycle contract.
        ship_retro_capture: Optional trigger fired once, on the edge a project
            first reaches COMPLETED, so finished work feeds a retrospective back
            into memory. ``None`` leaves the loop's consuming tail unwired.
        replan_trigger: Optional trigger fired while a plan reads as stalled, so
            an initiative that can no longer advance replans instead of hanging.
            ``None`` leaves a stalled plan for the operator to notice.
        integration: Optional INTEGRATE stage. ``None`` parks a plan that has
            built everything at INTEGRATING rather than completing it: an
            initiative whose pieces were never assembled has not delivered.
        evaluation: Optional EVALUATE stage, which owns the only transition
            that completes a plan. ``None`` parks a plan at EVALUATING: an
            initiative nobody scored has not been shown to meet its objective.
    """

    __slots__ = (
        "_clock",
        "_evaluation",
        "_integration",
        "_locks",
        "_persistence",
        "_plan_writer",
        "_replan_trigger",
        "_ship_retro_capture",
        "_task_engine",
    )

    def __init__(  # noqa: PLR0913 -- keyword-only dependency injection
        self,
        *,
        persistence: PersistenceBackend,
        plan_status_writer: PlanStatusWriter,
        clock: Clock,
        task_engine: TaskEngine | None = None,
        ship_retro_capture: RetroCapturePort | None = None,
        replan_trigger: ReplanTriggerPort | None = None,
        integration: IntegrationPort | None = None,
        evaluation: EvaluationPort | None = None,
    ) -> None:
        self._persistence = persistence
        self._plan_writer = plan_status_writer
        self._clock = clock
        self._task_engine = task_engine
        self._ship_retro_capture = ship_retro_capture
        self._replan_trigger = replan_trigger
        self._integration = integration
        self._evaluation = evaluation
        # The observer dispatch is sequential, so events alone cannot overlap
        # two recomputes for one plan. The tail stages can: each calls back in
        # once its verdict lands, from its own detached task. Cross-process
        # safety comes from the version-guarded writes, not this lock.
        self._locks: RefcountedLockMap[str] = RefcountedLockMap()

    def attach_tail(
        self,
        *,
        replan_trigger: ReplanTriggerPort | None = None,
        integration: IntegrationPort | None = None,
        evaluation: EvaluationFactory | None = None,
    ) -> None:
        """Fill in tail collaborators that a later boot phase resolved.

        The rollup is wired as soon as persistence and the task engine exist,
        which is before setup has configured a provider, so the first wire can
        legitimately produce a rollup with no tail. Re-running the wiring after
        setup must not re-register the observer, so it attaches here instead
        and the tail comes online without a restart.

        Only unset collaborators are filled: an already-wired stage keeps its
        instance, so a re-run never orphans one mid-flight. The evaluate stage
        arrives as a factory rather than an instance for that same reason: it
        captures the replan trigger permanently, so it must be built against
        whichever trigger this rollup ends up holding, not against one a
        caller built speculatively and this method then discarded.
        """
        if self._replan_trigger is None:
            self._replan_trigger = replan_trigger
        if self._integration is None:
            self._integration = integration
        if self._evaluation is None and evaluation is not None:
            self._evaluation = evaluation(self._replan_trigger)

    def has_full_tail(self) -> bool:
        """Whether every tail collaborator is wired.

        Returns:
            ``True`` when the replan trigger and both tail stages are present,
            so a re-wire can skip rebuilding them.
        """
        return (
            self._replan_trigger is not None
            and self._integration is not None
            and self._evaluation is not None
        )

    async def drain_retro_capture(self, *, timeout_sec: float) -> None:
        """Drain the SHIP-retro capture tail at shutdown, if one is wired.

        Delegated to the capture collaborator so an in-flight retrospective
        finishes (bounded by *timeout_sec*) before the memory backends it
        writes to are disconnected. A no-op when the tail is unwired.
        """
        if self._ship_retro_capture is None:
            return
        await self._ship_retro_capture.drain(timeout_sec=timeout_sec)

    async def drain_replan_trigger(self, *, timeout_sec: float) -> None:
        """Drain in-flight replans at shutdown, if a trigger is wired.

        A replan writes across the plan, project, and task graph, so an
        abandoned one at SIGTERM is exactly the partial state its compensated
        ordering exists to avoid. A no-op when the trigger is unwired.
        """
        if self._replan_trigger is None:
            return
        await self._replan_trigger.drain(timeout_sec=timeout_sec)

    async def drain_integration(self, *, timeout_sec: float) -> None:
        """Drain in-flight integration dispatches at shutdown, if wired.

        The dispatch persists a task and hands it to the work spine, so an
        abandoned one at SIGTERM could leave an integration task minted but
        never routed. A no-op when the stage is unwired.
        """
        if self._integration is None:
            return
        await self._integration.drain(timeout_sec=timeout_sec)

    async def drain_evaluation(self, *, timeout_sec: float) -> None:
        """Drain in-flight evaluations at shutdown, if a stage is wired.

        A judgement abandoned mid-flight loses the only verdict that can
        complete the initiative, so the plan would sit at EVALUATING until the
        next event re-fires it. A no-op when the stage is unwired.
        """
        if self._evaluation is None:
            return
        await self._evaluation.drain(timeout_sec=timeout_sec)

    async def on_task_state_changed(self, event: TaskStateChanged) -> None:
        """Recompute the initiative behind a task whose status changed.

        Best-effort by contract: never raises into the engine's observer
        dispatch, so a rollup failure cannot stall task processing. A failure
        is logged and self-heals on the next event for the same plan.
        """
        try:
            if event.task is None or event.new_status is None:
                return
            plan_id = event.task.plan_id
            if plan_id is None:
                # Not plan-driven work (a directly filed task), so there is no
                # initiative to roll up.
                return
            await self.recompute(plan_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort observer; heals next event
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                PROJECT_ROLLUP_FAILED,
                exc,
                task_id=event.task_id,
                new_status=event.new_status.value if event.new_status else None,
            )

    async def recompute(self, plan_id: UUID) -> None:
        """Derive and persist the plan and project status for *plan_id*.

        Idempotent: safe to call repeatedly, in any order, for the same plan.
        """
        async with self._locks.acquire(str(plan_id)):
            plan = await self._persistence.plans.get(NotBlankStr(str(plan_id)))
            if plan is None:
                logger.debug(
                    PROJECT_ROLLUP_SKIPPED, plan_id=str(plan_id), reason="missing"
                )
                return
            logger.debug(
                PROJECT_ROLLUP_STARTED,
                plan_id=str(plan_id),
                plan_status=plan.status.value,
            )
            started_as = plan.status
            items = await collect_item_progress(self._persistence, plan)
            item_count = len(items)
            if plan.status not in TERMINAL_STATUSES:
                derived = derive_plan_status(items, current=plan.status)
                if derived is not plan.status:
                    # A refused or contended plan write leaves *plan* at its
                    # last known status; the project still reconciles against
                    # that below rather than being skipped for this event.
                    plan = await self._advance_plan(plan, derived) or plan
                plan = await self._run_tail_stage(
                    plan, reopened=plan.status is not started_as
                )
            # A terminal plan still reconciles its project. The project write
            # can fail on the very event that terminalises the plan, and if a
            # terminal plan short-circuited here no later event could ever
            # repair it: the project would stay behind its plan permanently.
            # This read only picks the target. The edge test below uses the
            # status the winning write itself observed, so a project completed
            # between the two reads cannot swallow the retrospective.
            current = await self._project_status(plan)
            advance = await advance_project_status(
                self._persistence.projects,
                project_id=NotBlankStr(str(plan.project)),
                target=derive_project_status(plan.status, current=current),
            )
            project = advance.project
            before = advance.before if advance.before is not None else current
            await self._advance_parent_task(plan, items)
            self._maybe_trigger_replan(plan, items)
            self._maybe_capture_retro(plan, project, before=before)
            moved = plan.status is not started_as or (
                project is not None and project.status is not before
            )
            emit = logger.info if moved else logger.debug
            emit(
                PROJECT_ROLLUP_COMPLETED,
                plan_id=str(plan_id),
                plan_status=plan.status.value,
                project=str(plan.project),
                project_status=project.status.value if project else None,
                item_count=item_count,
            )

    async def _run_tail_stage(self, plan: Plan, *, reopened: bool) -> Plan:
        """Drive the tail stage *plan* currently sits in.

        The tail is where an initiative stops being a set of finished items and
        starts being a delivered thing, and each stage owns its own verdict:
        this only reads where the stage got to and moves the plan accordingly.
        Nothing here can complete a plan; only the evaluate stage's verdict
        can, which is what makes the tail unskippable.

        A stage that is unwired leaves the plan parked in that status with a
        warning on every recompute, deliberately: an initiative that cannot be
        integrated has not been integrated, and auto-completing it would be the
        exact lie this whole change removes.

        The two stages run in sequence rather than one per recompute, so a
        passing integration opens evaluation in the same pass: waiting for
        another task event would leave a delivered initiative idle until one
        happened to arrive.

        Returns:
            The plan, advanced when a stage produced a verdict.
        """
        if plan.status is PlanStatus.INTEGRATING:
            plan = await self._run_integration(plan, reopened=reopened)
        if plan.status is PlanStatus.EVALUATING:
            self._run_evaluation(plan)
        return plan

    async def _run_integration(self, plan: Plan, *, reopened: bool) -> Plan:
        """Fire or read the INTEGRATE stage for a plan sitting in it.

        *reopened* says whether this recompute is what put the plan into
        INTEGRATING. Only then may a spent assembly attempt be stepped over:
        that is the difference between reworking an item and re-running the
        same failed assembly on every event.

        Returns:
            The plan, advanced to EVALUATING when the assembly job passed.
        """
        if self._integration is None:
            logger.warning(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(plan.id),
                reason="integration_stage_unwired",
                note="plan parked at integrating; it will not auto-complete",
            )
            return plan
        state = await read_integration_state(
            self._persistence, plan, allow_new_attempt=reopened
        )
        if state.outcome in _DISPATCHABLE_OUTCOMES:
            self._integration.schedule(plan=plan, attempt=state.attempt)
            return plan
        if state.outcome is IntegrationOutcome.PASSED:
            return await self._advance_plan(plan, PlanStatus.EVALUATING) or plan
        if (
            state.outcome is IntegrationOutcome.FAILED
            and self._replan_trigger is not None
        ):
            # The pieces work and the whole does not, which no derivation over
            # items can see: every item is COMPLETED here.
            self._replan_trigger.schedule(
                plan=plan, reason=StallReason.INTEGRATION_FAILED
            )
            return plan
        if state.outcome is IntegrationOutcome.FAILED:
            # Same visible-park discipline as the unwired-stage branches: a
            # failed assembly with no trigger to route it cannot auto-replan,
            # so say so rather than returning an unchanged plan in silence.
            logger.warning(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(plan.id),
                reason="integration_failed_no_replan_trigger",
                note="plan parked at integrating; failed assembly cannot auto-replan",
            )
            return plan
        if state.outcome is IntegrationOutcome.RUNNING:
            # Logged rather than passed over in silence: an assembly job that
            # is genuinely working and one that died without terminalising its
            # row look identical from here, and this line is the only place an
            # operator can tell how long the plan has been waiting.
            logger.debug(
                PROJECT_ROLLUP_STARTED,
                plan_id=str(plan.id),
                plan_status=plan.status.value,
                note="integration job still running",
            )
        return plan

    def _run_evaluation(self, plan: Plan) -> None:
        """Fire the EVALUATE stage, or park the plan visibly.

        The stage owns the only transition that can complete a plan, so this
        never advances anything itself: it either hands the plan to the
        judgement or says loudly that no judgement can happen.
        """
        if self._evaluation is None:
            logger.warning(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(plan.id),
                reason="evaluation_stage_unwired",
                note="plan parked at evaluating; it will not auto-complete",
            )
            return
        self._evaluation.schedule(plan=plan)

    async def _advance_parent_task(
        self,
        plan: Plan,
        items: tuple[ItemProgress, ...],
    ) -> None:
        """Walk the objective task to the status its plan items imply.

        Coordination advances the parent once, when ``coordinate()`` returns,
        at which point its children are typically still ``IN_REVIEW``: it can
        therefore never land the parent's terminal status without reading an
        unverified run outcome. Re-deriving it here, on the same recompute
        that already reads persisted child status, lets the parent finish
        honestly once the review gate has ruled on every child.

        The objective task is the initiative on the board, so it is held open
        for exactly as long as the plan is: every item passing its own gate
        does not deliver the objective, the tail does, and one item failing
        does not end the objective while its siblings are still building. The
        walk therefore stops short of any finished-looking status until the
        plan itself is COMPLETED, while the rollup counts it records stay the
        children's real ones.

        A superseded plan is skipped entirely. Its successor owns the
        objective, and the replan that superseded it cancels the retired
        items, so deriving from them here would walk the objective task to a
        truly terminal CANCELLED that the successor could never reopen.

        Best-effort and idempotent, like the rest of the recompute: an
        unreachable target or a rejected hop is logged and repaired by the
        next event.
        """
        if self._task_engine is None or not items:
            return
        if plan.status is PlanStatus.SUPERSEDED:
            logger.debug(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(plan.id),
                reason="superseded_plan_no_longer_owns_objective",
            )
            return
        live = await self._task_engine.get_task(plan.parent_task_id)
        if live is None:
            logger.debug(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(plan.id),
                reason="parent_task_missing",
            )
            return
        rollup = StatusRollup.compute(
            NotBlankStr(plan.parent_task_id),
            tuple(_parent_status_of(item) for item in items),
        )
        derived = rollup.derived_parent_status
        held = (
            derived in _OBJECTIVE_FINISHED_STATUSES
            and plan.status is not PlanStatus.COMPLETED
        )
        outcome = await advance_parent_to_rollup_status(
            self._task_engine,
            task_id=plan.parent_task_id,
            current_status=live.status,
            rollup=rollup,
            target=TaskStatus.IN_PROGRESS if held else derived,
        )
        if not outcome.success:
            logger.debug(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(plan.id),
                reason="parent_walk_refused",
                note=outcome.error,
            )

    def _maybe_trigger_replan(
        self,
        plan: Plan,
        items: tuple[ItemProgress, ...],
    ) -> None:
        """Fire the replan trigger while *plan* reads as stalled.

        Deliberately not edge-gated here. A stall has no persisted marker to
        compare against, and the honest guard is the one the trigger already
        needs: it re-reads the plan, refuses anything no longer replannable,
        and collapses a duplicate while one is in flight. A successful replan
        supersedes the plan, so the next recompute finds nothing to do.

        The trigger schedules detached work and never raises, so it is safe on
        this best-effort path.
        """
        if self._replan_trigger is None or plan.status in TERMINAL_STATUSES:
            return
        reason = stall_reason(items)
        if reason is None:
            return
        self._replan_trigger.schedule(plan=plan, reason=reason)

    def _maybe_capture_retro(
        self,
        plan: Plan,
        project: Project | None,
        *,
        before: ProjectStatus,
    ) -> None:
        """Fire the retrospective trigger on the edge into COMPLETED.

        Only the transition fires it, never a project already terminal, so a
        redelivered event or a recompute over a finished project does not
        re-trigger. The trigger schedules detached work and never raises, so it
        is safe on this best-effort path.
        """
        if (
            self._ship_retro_capture is None
            or project is None
            or before is ProjectStatus.COMPLETED
            or project.status is not ProjectStatus.COMPLETED
        ):
            return
        self._ship_retro_capture.schedule(plan=plan, project=project)

    async def _advance_plan(self, plan: Plan, target: PlanStatus) -> Plan | None:
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

        Returns:
            The persisted plan, or ``None`` when the transition was refused or
            the write stayed contended for the whole retry budget.
        """
        current = plan
        explicit_target = target
        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            try:
                return await self._walk_plan_to(current, target)
            except VersionConflictError:
                # Must precede the ConflictError handler: VersionConflictError
                # subclasses it, so catching the base first would strand every
                # version conflict in the illegal-transition branch and the
                # CAS retry below would never run.
                logger.info(
                    PROJECT_ROLLUP_CONFLICT_RETRY,
                    plan_id=str(current.id),
                    attempt=attempt,
                    operation="plan_status",
                )
                refreshed = await self._persistence.plans.get(
                    NotBlankStr(str(current.id))
                )
                if refreshed is None:
                    return None
                if refreshed.status in TERMINAL_STATUSES:
                    # The winner finished the plan; its state is authoritative
                    # and the project reconcile below runs against it.
                    return refreshed
                if (
                    explicit_target is PlanStatus.EVALUATING
                    and refreshed.status is PlanStatus.INTEGRATING
                ):
                    # ``derive_plan_status`` never emits EVALUATING, so
                    # re-deriving here would collapse an explicit
                    # INTEGRATING -> EVALUATING write back to INTEGRATING and
                    # skip the evaluate stage. The winner left the plan at
                    # INTEGRATING, so the caller's tail target is still legal.
                    target = PlanStatus.EVALUATING
                else:
                    items = await collect_item_progress(self._persistence, refreshed)
                    target = derive_plan_status(items, current=refreshed.status)
                if target is refreshed.status:
                    return refreshed
                current = refreshed
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

    async def _walk_plan_to(self, plan: Plan, target: PlanStatus) -> Plan:
        """Move *plan* to *target* one legal hop at a time.

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
            current = await self._plan_writer.sync_status(
                current, hop, requested_by=_ACTOR
            )
        return current

    async def _project_status(self, plan: Plan) -> ProjectStatus:
        """Read the current status of the plan's project.

        Returns:
            The project's status, or ``PLANNING`` when it no longer exists
            (the subsequent write is then a no-op).
        """
        project = await self._persistence.projects.get(NotBlankStr(str(plan.project)))
        if project is None:
            logger.debug(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(plan.id),
                project=str(plan.project),
                reason="project_missing",
            )
            return ProjectStatus.PLANNING
        return project.status
