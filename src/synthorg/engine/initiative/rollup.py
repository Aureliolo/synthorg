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

from uuid import UUID

from synthorg.core.clock import Clock
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import TERMINAL_STATUSES, PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import (
    ItemProgress,
    ReplanDisposition,
    StallReason,
    derive_plan_status,
    derive_project_status,
    stall_reason,
)
from synthorg.engine.initiative.item_progress import collect_item_progress
from synthorg.engine.initiative.ports import (
    EvaluationPort,
    IntegrationPort,
    PlanStatusWriter,
    ReplanTriggerPort,
    RetroCapturePort,
    SkeletonPort,
    StagePorts,
)
from synthorg.engine.initiative.project_writes import advance_project_status
from synthorg.engine.initiative.rollup_parent_task import advance_objective_task
from synthorg.engine.initiative.rollup_plan_advance import advance_plan
from synthorg.engine.initiative.rollup_stages import (
    StallRoute,
    drive_evaluation,
    drive_integration,
    drive_skeleton,
)
from synthorg.engine.initiative.stall_escalation import StallEscalationService
from synthorg.engine.initiative.stall_route import escalate_stall, route_stall
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.project import (
    PROJECT_ROLLUP_COMPLETED,
    PROJECT_ROLLUP_FAILED,
    PROJECT_ROLLUP_SKIPPED,
    PROJECT_ROLLUP_STARTED,
)
from synthorg.persistence.lifecycle_ledger import ledger_for
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


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
        replan_trigger: Optional trigger asked while a plan reads as stalled, so
            an initiative that can no longer advance replans instead of hanging.
            It answers with a disposition rather than acting silently, and its
            two refusals (the master switch, the generation cap) route to the
            escalation exactly as its absence does.
        stages: The staged jobs a plan can sit in. Any of them unwired parks
            the plan in that status rather than advancing past it: an
            initiative whose contract was never written has not been built, and
            one whose pieces were never assembled has not delivered. Each
            arrives in a later boot phase, so a rollup built before them has
            none and fills them through :meth:`attach_tail`.

    The stall escalation, the owner of "this initiative has no automatic route
    left", is attached later through :meth:`attach_tail` because it needs the
    approval store. Unattached, a stall fails the plan with its reason, which
    is the fail-closed answer for a deployment where nothing can ask a human:
    parking it silently is the deadlock the visible-park discipline prevents.
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
        "_skeleton",
        "_stall_escalation",
        "_task_engine",
    )

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        plan_status_writer: PlanStatusWriter,
        clock: Clock,
        task_engine: TaskEngine | None = None,
        ship_retro_capture: RetroCapturePort | None = None,
        replan_trigger: ReplanTriggerPort | None = None,
        stages: StagePorts | None = None,
    ) -> None:
        self._persistence = persistence
        self._plan_writer = plan_status_writer
        self._clock = clock
        self._task_engine = task_engine
        self._ship_retro_capture = ship_retro_capture
        self._replan_trigger = replan_trigger
        resolved = stages if stages is not None else StagePorts()
        self._skeleton = resolved.skeleton
        self._integration = resolved.integration
        self._evaluation = resolved.evaluation
        # Attached only through ``attach_tail``: it needs the approval store,
        # which the boot phase that builds this service has not reached.
        self._stall_escalation: StallEscalationService | None = None
        # The observer dispatch is sequential, so events alone cannot overlap
        # two recomputes for one plan. The tail stages can: each calls back in
        # once its verdict lands, from its own detached task. Cross-process
        # safety comes from the version-guarded writes, not this lock.
        self._locks: RefcountedLockMap[str] = RefcountedLockMap()

    def attach_tail(
        self,
        *,
        replan_trigger: ReplanTriggerPort | None = None,
        skeleton: SkeletonPort | None = None,
        integration: IntegrationPort | None = None,
        evaluation: EvaluationPort | None = None,
        ship_retro_capture: RetroCapturePort | None = None,
        stall_escalation: StallEscalationService | None = None,
    ) -> None:
        """Fill in stage collaborators that a later boot phase resolved.

        The rollup is wired as soon as persistence and the task engine exist,
        which is before setup has configured a provider, so the first wire can
        legitimately produce a rollup with no tail. Re-running the wiring after
        setup must not re-register the observer, so it attaches here instead
        and the tail comes online without a restart.

        Each collaborator has its own subsystem and arrives on its own
        schedule, so a call fills only what it was handed. Only unset ones are
        filled: an already-wired stage keeps its instance, so a re-run never
        orphans one mid-flight.

        The retrospective capture is filled here for the same reason as the
        three stages: it needs the provider and agent registries too, so a
        rollup built before either existed has none, and leaving it to the
        constructor alone would strand the consuming tail permanently.
        """
        if self._replan_trigger is None:
            self._replan_trigger = replan_trigger
        if self._skeleton is None:
            self._skeleton = skeleton
        if self._integration is None:
            self._integration = integration
        if self._evaluation is None:
            self._evaluation = evaluation
        if self._ship_retro_capture is None:
            self._ship_retro_capture = ship_retro_capture
        if self._stall_escalation is None:
            self._stall_escalation = stall_escalation

    async def detach_retro_capture(self, *, timeout_sec: float) -> None:
        """Drain and drop the retrospective capture, so a pass can rebuild it.

        It captured both memory backends at construction, so once either is
        replaced the capture writes into layers nothing else reads. Dropping it
        is also what the reconciler reads as this subsystem being down, since
        liveness comes from the rollup's own attachment record. Drained before
        it is released: an in-flight retrospective abandoned mid-write is the
        partial state the shutdown drain exists to avoid, and a rebuild is no
        different.

        The slot is cleared BEFORE the drain, not after. The drain awaits, and
        the rollup shares an event loop with the task-state callback, so a
        recompute arriving during that await would otherwise still find this
        capture and schedule onto it: a retrospective started on an instance
        already being dropped, never drained again, writing into the very
        backends the rebuild exists to replace. Clearing first also makes the
        liveness probe report the subsystem down from the moment teardown
        begins rather than when it finishes.
        """
        capture = self._ship_retro_capture
        if capture is None:
            return
        self._ship_retro_capture = None
        await capture.drain(timeout_sec=timeout_sec)

    def replan_trigger(self) -> ReplanTriggerPort | None:
        """Return the attached replan trigger, or ``None``.

        The EVALUATE stage reads it through this rather than capturing one at
        construction: the two subsystems attach independently, so a stage built
        before the coordinator existed would otherwise hold ``None`` for the
        life of the process.

        Returns:
            The trigger the rollup currently holds.
        """
        return self._replan_trigger

    def has_replan_trigger(self) -> bool:
        """Whether the stalled-initiative replan trigger is attached.

        Returns:
            ``True`` once the trigger is present.
        """
        return self._replan_trigger is not None

    def has_skeleton(self) -> bool:
        """Whether the SKELETON stage is attached.

        Returns:
            ``True`` once the stage is present.
        """
        return self._skeleton is not None

    def has_integration(self) -> bool:
        """Whether the INTEGRATE stage is attached.

        Returns:
            ``True`` once the stage is present.
        """
        return self._integration is not None

    def has_evaluation(self) -> bool:
        """Whether the EVALUATE stage is attached.

        Returns:
            ``True`` once the stage is present.
        """
        return self._evaluation is not None

    def has_stall_escalation(self) -> bool:
        """Whether the stalled-initiative escalation is attached.

        Its own liveness rather than the replan trigger's: the two answer
        different questions and converge separately, and an initiative whose
        trigger refuses still needs somebody to raise the decision. Folding
        them into one probe would let a boot with a trigger read as covered
        while nothing could ask a human at all.

        Returns:
            ``True`` once the escalation collaborator is present.
        """
        return self._stall_escalation is not None

    def has_retro_capture(self) -> bool:
        """Whether the SHIP-time retrospective capture is attached.

        Read as its own liveness rather than folded into the stages': it is
        built from the memory backends, which converge on their own schedule,
        so counting it under a stage's probe would let the reconciler call a
        tail converged while the retrospective silently never fires.

        Returns:
            ``True`` once the capture collaborator is present.
        """
        return self._ship_retro_capture is not None

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

    async def report_stage_stall(
        self,
        plan_id: UUID,
        reason: StallReason,
        disposition: ReplanDisposition | None,
    ) -> None:
        """Escalate a stall only a tail stage could see.

        A tail-stage verdict is invisible to any derivation over items, since
        every item is done in both cases, so ``recompute`` cannot find it and
        the stage has to hand it over. The stage has already asked the trigger
        (it holds the judged evidence the replan brief wants), so the answer
        travels with the report rather than being asked for a second time.

        Args:
            plan_id: The initiative that cannot advance.
            reason: The tail-stage verdict.
            disposition: What the trigger answered the stage, or ``None`` when
                the stage found no trigger at all.
        """
        async with self._locks.acquire(str(plan_id)):
            plan = await self._persistence.plans.get(NotBlankStr(str(plan_id)))
            if plan is None:
                logger.debug(
                    PROJECT_ROLLUP_SKIPPED, plan_id=str(plan_id), reason="missing"
                )
                return
            await escalate_stall(
                plan,
                reason,
                disposition=disposition,
                items=None,
                escalation=self._stall_escalation,
                fail_plan=self._fail_plan,
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
                plan = await self._run_stage(
                    plan, reopened=plan.status is not started_as
                )
            # A terminal plan still reconciles its project. The project write
            # can fail on the very event that terminalises the plan, and if a
            # terminal plan short-circuited here no later event could ever
            # repair it: the project would stay behind its plan permanently.
            # This read only picks the target. The edge test below uses the
            # status the winning write itself observed, so a project completed
            # between the two reads cannot swallow the retrospective.
            plan = await self._resolve_stall(plan, items)
            current = await self._project_status(plan)
            advance = await advance_project_status(
                self._persistence.projects,
                project_id=NotBlankStr(str(plan.project)),
                target=derive_project_status(plan.status, current=current),
                ledger=ledger_for(self._persistence, clock=self._clock),
            )
            project = advance.project
            before = advance.before if advance.before is not None else current
            await advance_objective_task(self._task_engine, plan, items)
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

    async def _run_stage(self, plan: Plan, *, reopened: bool) -> Plan:
        """Drive whichever staged job *plan* currently sits in.

        A stage owns its own verdict, so this only reads where the stage got to
        and moves the plan accordingly. Nothing here can complete a plan; only
        the evaluate stage's verdict can, which is what makes the tail
        unskippable, and nothing here can dispatch a unit; only the skeleton
        passing can, which is what makes the contract unskippable.

        The stages run in sequence rather than one per recompute, so a passing
        integration opens evaluation in the same pass: waiting for another task
        event would leave a delivered initiative idle until one happened to
        arrive. The head is checked first and the plan leaves it in the same
        pass, so a passing skeleton reaches EXECUTING without a second event.

        Returns:
            The plan, advanced when a stage produced a verdict.
        """
        if plan.status is PlanStatus.SKELETON:
            plan = await drive_skeleton(
                plan,
                persistence=self._persistence,
                skeleton=self._skeleton,
                reopened=reopened,
                advance=self._advance_plan,
                stall=self._stall_on_stage_failure(StallReason.SKELETON_FAILED),
            )
        if plan.status is PlanStatus.INTEGRATING:
            plan = await drive_integration(
                plan,
                persistence=self._persistence,
                integration=self._integration,
                reopened=reopened,
                advance=self._advance_plan,
                stall=self._stall_on_stage_failure(StallReason.INTEGRATION_FAILED),
            )
        if plan.status is PlanStatus.EVALUATING:
            drive_evaluation(plan, evaluation=self._evaluation)
        return plan

    def _stall_on_stage_failure(self, reason: StallReason) -> StallRoute:
        """Bind the stall route to one stage's failure reason.

        Returns:
            A callable routing a plan to a replan or to the operator.
        """

        async def _route(plan: Plan) -> Plan:
            return await route_stall(
                plan,
                reason,
                items=None,
                trigger=self._replan_trigger,
                escalation=self._stall_escalation,
                fail_plan=self._fail_plan,
            )

        return _route

    async def _resolve_stall(
        self,
        plan: Plan,
        items: tuple[ItemProgress, ...],
    ) -> Plan:
        """Route a stalled plan to a replan, or to the operator.

        A stall means every outstanding item is dead: the initiative cannot
        advance and nothing will move it.

        Asking is deliberately not edge-gated. A stall has no persisted marker
        to compare against, and the honest guards are the ones the collaborators
        already need: the trigger re-reads the plan and collapses a duplicate
        while one is in flight, and the escalation refuses to raise a second
        decision while the first is open. A successful replan supersedes the
        plan, so the next recompute finds nothing to do.

        Returns:
            The plan, as the route left it.
        """
        if plan.status in TERMINAL_STATUSES:
            return plan
        reason = stall_reason(items)
        if reason is None:
            return plan
        return await route_stall(
            plan,
            reason,
            items=items,
            trigger=self._replan_trigger,
            escalation=self._stall_escalation,
            fail_plan=self._fail_plan,
        )

    async def _fail_plan(self, plan: Plan, failure_reason: NotBlankStr) -> Plan | None:
        """End a plan the stall route could not put in front of anybody.

        Returns:
            The persisted plan, or ``None`` when the transition was refused.
        """
        return await self._advance_plan(
            plan, PlanStatus.FAILED, failure_reason=failure_reason
        )

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

    async def _advance_plan(
        self,
        plan: Plan,
        target: PlanStatus,
        *,
        failure_reason: NotBlankStr | None = None,
    ) -> Plan | None:
        """Persist the plan's derived status through the audited write path.

        Returns:
            The persisted plan, or ``None`` when the transition was refused or
            the write stayed contended for the whole retry budget.
        """
        return await advance_plan(
            self._persistence,
            self._plan_writer,
            plan,
            target,
            failure_reason=failure_reason,
        )

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
