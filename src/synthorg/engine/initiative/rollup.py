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

**It reads persisted task status, never execution outcomes.** A task only
reaches ``COMPLETED`` through the review gate, which runs the completion-oracle
chain. Deriving from persisted status therefore composes with the verify gate
for free: an initiative cannot complete on work that merely executed. The
coordination-level parent rollup derives from ``DispatchResult`` outcomes
instead, which report success before verification, so this service deliberately
does not reuse it.
"""

from typing import Final
from uuid import UUID

from synthorg.core.clock import Clock
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError, VersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import TERMINAL_STATUSES, PlanItemKind, PlanStatus
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.initiative.completion import (
    ItemProgress,
    derive_plan_status,
    derive_project_status,
)
from synthorg.engine.initiative.ports import PlanStatusWriter
from synthorg.engine.initiative.project_writes import advance_project_status
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.project import (
    PROJECT_ROLLUP_COMPLETED,
    PROJECT_ROLLUP_FAILED,
    PROJECT_ROLLUP_SKIPPED,
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
    """

    __slots__ = ("_clock", "_locks", "_persistence", "_plan_writer")

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        plan_status_writer: PlanStatusWriter,
        clock: Clock,
    ) -> None:
        self._persistence = persistence
        self._plan_writer = plan_status_writer
        self._clock = clock
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
            if plan is None or plan.status in TERMINAL_STATUSES:
                logger.debug(
                    PROJECT_ROLLUP_SKIPPED,
                    plan_id=str(plan_id),
                    reason="missing" if plan is None else "terminal",
                )
                return
            items = await self._collect_item_progress(plan)
            derived = derive_plan_status(items, current=plan.status)
            if derived is not plan.status:
                written = await self._advance_plan(plan, derived)
                if written is None:
                    return
                plan = written
            project = await advance_project_status(
                self._persistence.projects,
                project_id=NotBlankStr(str(plan.project)),
                target=derive_project_status(
                    plan.status,
                    current=await self._project_status(plan),
                ),
            )
            logger.info(
                PROJECT_ROLLUP_COMPLETED,
                plan_id=str(plan_id),
                plan_status=plan.status.value,
                project=str(plan.project),
                project_status=project.status.value if project else None,
                item_count=len(items),
            )

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

        Returns:
            The persisted plan, or ``None`` when the write was refused or a
            concurrent write won (the next event recomputes from the winner).
        """
        try:
            return await self._plan_writer.sync_status(
                plan, target, requested_by=_ACTOR
            )
        except (ConflictError, VersionConflictError) as exc:
            logger.info(
                PROJECT_ROLLUP_SKIPPED,
                plan_id=str(plan.id),
                reason=type(exc).__name__,
            )
            return None

    async def _project_status(self, plan: Plan) -> ProjectStatus:
        """Read the current status of the plan's project.

        Returns:
            The project's status, or ``PLANNING`` when it no longer exists
            (the subsequent write is then a no-op).
        """
        project = await self._persistence.projects.get(NotBlankStr(str(plan.project)))
        return project.status if project is not None else ProjectStatus.PLANNING
