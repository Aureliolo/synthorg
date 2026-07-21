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
complete on work that merely executed. The coordination-level parent rollup
derives from ``DispatchResult`` outcomes instead, which report success before
verification, so this service deliberately does not reuse it.

That composition is a property of which writers are wired, not a structural
guarantee of the status field. Two other paths reach ``COMPLETED`` without the
oracle chain: ``workers/execution_service/_lifecycle.py`` (the lifecycle-only
baseline the app self-constructs when no agent runtime is installed) and the
coordination parent rollup above. Both are legitimate in their own context;
neither should drive an initiative whose completion is meant to be verified.
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
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.initiative.completion import (
    ItemProgress,
    derive_plan_status,
    derive_project_status,
)
from synthorg.engine.initiative.ports import PlanStatusWriter, RetroCapturePort
from synthorg.engine.initiative.project_writes import (
    MAX_WRITE_ATTEMPTS,
    advance_project_status,
)
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
from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)

#: Identity recorded on rollup-driven status writes, so the audit log
#: distinguishes a derived transition from an operator decision.
_ACTOR: Final[str] = "initiative-rollup"

#: Page size for draining a plan's tasks. A plan's item count is bounded well
#: below this at the request boundary, so one page is the normal case and the
#: loop is a guard rather than an expected path.
_TASK_PAGE_SIZE: Final[int] = 200


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
    """

    __slots__ = (
        "_clock",
        "_locks",
        "_persistence",
        "_plan_writer",
        "_ship_retro_capture",
    )

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        plan_status_writer: PlanStatusWriter,
        clock: Clock,
        ship_retro_capture: RetroCapturePort | None = None,
    ) -> None:
        self._persistence = persistence
        self._plan_writer = plan_status_writer
        self._clock = clock
        self._ship_retro_capture = ship_retro_capture
        # Serialises same-process recomputes for one plan so concurrent task
        # completions do not each read the pre-write state. Cross-process
        # safety comes from the version-guarded writes, not this lock.
        self._locks: RefcountedLockMap[str] = RefcountedLockMap()

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
            item_count = 0
            if plan.status not in TERMINAL_STATUSES:
                items = await self._collect_item_progress(plan)
                item_count = len(items)
                derived = derive_plan_status(items, current=plan.status)
                if derived is not plan.status:
                    # A refused or contended plan write leaves *plan* at its
                    # last known status; the project still reconciles against
                    # that below rather than being skipped for this event.
                    plan = await self._advance_plan(plan, derived) or plan
            # A terminal plan still reconciles its project. The project write
            # can fail on the very event that terminalises the plan, and if a
            # terminal plan short-circuited here no later event could ever
            # repair it: the project would stay behind its plan permanently.
            before = await self._project_status(plan)
            project = await advance_project_status(
                self._persistence.projects,
                project_id=NotBlankStr(str(plan.project)),
                target=derive_project_status(plan.status, current=before),
            )
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

    async def _collect_item_progress(self, plan: Plan) -> tuple[ItemProgress, ...]:
        """Pair each plan item with the live status of its dispatched task.

        Returns:
            One :class:`ItemProgress` per plan item, in plan order.
        """
        by_item = await self._tasks_by_item(plan)
        progress: list[ItemProgress] = []
        for item in plan.items:
            item_uuid = subtask_uuid(item.id)
            task = by_item.get(item_uuid)
            progress.append(
                ItemProgress(
                    item_id=item_uuid,
                    kind=item.kind,
                    task_id=task.id if task is not None else None,
                    task_status=task.status if task is not None else None,
                    chosen_option_id=(
                        item.chosen_option_id
                        if item.kind is PlanItemKind.DECISION
                        else None
                    ),
                )
            )
        return tuple(progress)

    async def _tasks_by_item(self, plan: Plan) -> dict[UUID, Task]:
        """Index the plan's dispatched tasks by the item each implements.

        Returns:
            Map of plan-item id to the task implementing it.
        """
        indexed: dict[UUID, Task] = {}
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded by plan size
        while True:
            page = await self._persistence.tasks.query(
                TaskFilterSpec(plan=plan.id),
                limit=_TASK_PAGE_SIZE,
                offset=offset,
            )
            for task in page:
                if task.plan_item_id is not None:
                    indexed[task.plan_item_id] = task
            if len(page) < _TASK_PAGE_SIZE:
                return indexed
            offset += _TASK_PAGE_SIZE

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
        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            try:
                return await self._walk_plan_to(current, target)
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
            except VersionConflictError:
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
                items = await self._collect_item_progress(refreshed)
                target = derive_plan_status(items, current=refreshed.status)
                if target is refreshed.status:
                    return refreshed
                current = refreshed
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
