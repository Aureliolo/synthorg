# module-kind: service
"""Pick up the runs a stopped process left behind.

A plan's waves are driven by a background task, started when an operator
approves the plan. That is an edge, and an edge does not survive the process
that took it: once the task is gone, nothing anywhere is left asking whether
the plan still needs driving. A live run ended with two subtasks at
``in_progress``, one at ``in_review`` and a plan at ``executing``, and the
board went on showing work in flight with nothing behind it. Restarting is an
ordinary operator action (an upgrade, a settings change that needs a rebuild,
a crash the runtime restarts), so this is not an exotic failure: it is what a
restart did, every time, silently.

So this asks the question on a cadence instead, which is the same shape the
subsystem reconciler uses for wiring: boot is the first pass, and every later
pass is the same idempotent question with a different label. For each plan
that is not finished it asks what would have to happen for the plan to move,
and does it.

Every plan status gets an answer here, because a status this module does not
name is a status nothing is watching, which is the defect rather than a gap in
it. The answers are deliberately different in kind:

- A plan awaiting a human is not stuck; it is waiting correctly.
- A dispatched plan whose waves have nobody driving them is resumed, and its
  orphaned tasks are requeued first so the waves have something to dispatch.
- A tail-stage plan needs no driver at all: the tail's own stages are keyed on
  a derived id and read their own state, so one rollup pass re-drives them.
- A ``PLANNING`` shell is the one that cannot be resumed. Its items were being
  written by the intake pipeline, which holds a brief this has no way to
  recover, so it is failed with a reason that says exactly that: an operator
  gets a plan they can read and delete instead of a shell that never fills.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Final, NamedTuple, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import (
    TAIL_STATUSES,
    TERMINAL_STATUSES,
    PlanStatus,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dependency_gate import awaits_dispatch
from synthorg.engine.coordination.run_ledger import LiveRunLedger
from synthorg.engine.initiative.item_progress import TASK_PAGE_SIZE
from synthorg.engine.initiative.tail_stages import is_integration_task
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TransitionTaskMutation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.run_recovery import (
    RUN_RECOVERY_PLAN_FAILED,
    RUN_RECOVERY_PLAN_RESUMED,
    RUN_RECOVERY_PLAN_SKIPPED,
    RUN_RECOVERY_SWEEP_COMPLETE,
    RUN_RECOVERY_SWEEP_FAILED,
    RUN_RECOVERY_SWEEP_STARTED,
    RUN_RECOVERY_TASK_REQUEUED,
)
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)

#: Recorded on every write this sweep makes, so an operator reading a task's
#: history sees that a restart moved it rather than an agent or themselves.
RECOVERY_ACTOR: Final[str] = "run-recovery"

#: Statuses whose plan has a dispatch that somebody has to be driving.
#: ``APPROVED`` is here beside ``EXECUTING`` because the window between the
#: two is exactly where a dispatch dies without having written anything: the
#: approval returned, the background task was created, and the process went
#: away before the first wave moved the plan on.
DRIVEN_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.APPROVED, PlanStatus.EXECUTING}
)

#: Statuses whose plan is parked on a person. Nothing is wrong with these and
#: nothing is done to them: the operator's decision is the trigger.
AWAITING_HUMAN_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.DRAFT, PlanStatus.PENDING_REVIEW}
)

#: Statuses whose plan has items nobody finished writing. There is no way back
#: to the pipeline that was writing them, so the plan is failed rather than
#: left as a shell that can never fill.
UNFILLED_STATUSES: Final[frozenset[PlanStatus]] = frozenset({PlanStatus.PLANNING})

#: Statuses in which a task belonging to a driven plan was mid-flight when the
#: process holding its runner went away. Requeued so the resumed waves have
#: something to dispatch; ``ASSIGNED`` counts because the row was handed to a
#: runner that no longer exists, which is the same fact one step earlier.
ORPHANED_TASK_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}
)

_REQUEUE_REASON: Final[str] = (
    "Requeued: the process running this work stopped before it finished, so "
    "the row was left with nothing driving it"
)

_UNFILLED_REASON: Final[NotBlankStr] = NotBlankStr(
    "Decomposition did not survive a restart: the plan was still being "
    "written when the process stopped, and the brief it was written from is "
    "not recoverable. File the initiative again."
)


class _RevivedRows(NamedTuple):
    """What one plan's rows needed, and what is left to run.

    Attributes:
        requeued: Rows moved out of an orphaned in-flight status.
        rejudged: Reviews asked for again.
        dispatchable: Whether any row still awaits a wave. ``False`` means
            the plan's answer is whatever the rollup derives from rows that
            are finished, dead, or parked on somebody, and driving it would
            gate every wave out and change nothing.
    """

    requeued: int
    rejudged: int
    dispatchable: bool


@runtime_checkable
class PlanDriver(Protocol):
    """Runs a dispatched plan's remaining waves.

    A port rather than a call, because driving a plan needs the coordinator,
    the agent roster and the objective task, which are assembled in the API
    layer. Stating it here keeps the sweep's own dependencies to the graph it
    reads.
    """

    async def __call__(self, plan: Plan) -> bool:
        """Drive *plan*'s remaining waves to whatever they reach.

        Returns:
            Whether a drive now owns the plan. ``False`` says the driver
            declined and nothing is running, which the sweep must report as a
            skip: the driver is the only one that knows, and a caller that
            assumed a resume logged one on every pass for a plan whose
            objective task was gone, so a permanently undrivable run read as
            being rescued every ten minutes for ever.
        """
        ...


class RunRecoveryReport(BaseModel):
    """What one sweep found and did.

    Attributes:
        plans_seen: Non-terminal plans the sweep considered.
        resumed: Plans whose waves were handed back to a driver.
        recomputed: Tail-stage plans whose stage was re-driven.
        requeued: Tasks moved out of an orphaned in-flight status.
        rejudged: Tasks in review whose verdict was asked for again.
        failed: Unfillable shells failed with a reason.
        skipped: Plans left alone: parked on a human, already being driven,
            or handed to a driver that declined.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    plans_seen: int = Field(ge=0, description="Non-terminal plans considered")
    resumed: int = Field(ge=0, description="Plans handed back to a driver")
    recomputed: int = Field(ge=0, description="Tail-stage plans re-driven")
    requeued: int = Field(ge=0, description="Orphaned tasks requeued")
    rejudged: int = Field(ge=0, description="Reviews asked for again")
    failed: int = Field(ge=0, description="Unfillable shells failed")
    skipped: int = Field(ge=0, description="Plans deliberately left alone")


class RunRecoveryReconciler:
    """Asks, for every unfinished plan, whether anything is still driving it.

    Args:
        persistence: Reads plans and their tasks, and writes a failed shell.
        task_engine: Owns task status, so every requeue goes through it.
        ledger: The one owner of "is this plan being driven in this process",
            so a periodic pass cannot start a second driver on a plan the
            approval path is already running.
        drive_plan: Runs a dispatched plan's remaining waves.
        recompute_plan: Re-derives a plan and re-drives its tail stage.
        rejudge_task: Asks the completion gates again for a row left in
            review by a session that stopped. ``None`` when no review gate is
            wired, which leaves those rows where they are rather than moving
            work somewhere nothing judges it.
        open_decisions: Reads which tasks currently have a decision open
            against them, so a row waiting on a PERSON is told apart from one
            waiting on nobody. ``None`` leaves every row in review alone,
            which is the fail-closed direction.
        defers_to_queue: Whether execution is handed to a distributed work
            queue. When it is, an in-flight row may be owned by a live worker
            in another process and redelivery is that queue's job, so this
            sweep requeues nothing and says so rather than adding a second
            answer to the same question.
    """

    __slots__ = (
        "_defers_to_queue",
        "_drive_plan",
        "_ledger",
        "_open_decisions",
        "_persistence",
        "_recompute_plan",
        "_rejudge_task",
        "_task_engine",
    )

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        task_engine: TaskEngine,
        ledger: LiveRunLedger,
        drive_plan: PlanDriver,
        recompute_plan: Callable[[Plan], Awaitable[None]],
        rejudge_task: Callable[[Task], Awaitable[None]] | None = None,
        open_decisions: Callable[[], Awaitable[frozenset[str]]] | None = None,
        defers_to_queue: bool = False,
    ) -> None:
        self._persistence = persistence
        self._task_engine = task_engine
        self._ledger = ledger
        self._drive_plan = drive_plan
        self._recompute_plan = recompute_plan
        self._rejudge_task = rejudge_task
        self._open_decisions = open_decisions
        self._defers_to_queue = defers_to_queue

    async def reconcile(self, *, trigger: str) -> RunRecoveryReport:
        """Run one pass over every unfinished plan.

        Idempotent: a plan already being driven is skipped, a plan with
        nothing left to dispatch resumes into waves that dispatch nothing,
        and a requeue of a row already requeued is refused by the engine.

        Args:
            trigger: What caused this pass, for the log (``boot`` or
                ``periodic``).

        Returns:
            What the pass found and did.
        """
        logger.info(RUN_RECOVERY_SWEEP_STARTED, trigger=trigger)
        plans = await self._unfinished_plans()
        resumed = recomputed = requeued = rejudged = failed = skipped = 0
        for plan in plans:
            try:
                outcome = await self._reconcile_one(plan)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- one unreadable plan must not stop
                # the sweep; every other stranded run still needs picking up
                reraise_critical(exc)
                logger.warning(
                    RUN_RECOVERY_SWEEP_FAILED,
                    plan_id=str(plan.id),
                    plan_status=plan.status.value,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            resumed += outcome.resumed
            recomputed += outcome.recomputed
            requeued += outcome.requeued
            rejudged += outcome.rejudged
            failed += outcome.failed
            skipped += outcome.skipped
        report = RunRecoveryReport(
            plans_seen=len(plans),
            resumed=resumed,
            recomputed=recomputed,
            requeued=requeued,
            rejudged=rejudged,
            failed=failed,
            skipped=skipped,
        )
        acted = resumed or recomputed or requeued or rejudged or failed
        emit = logger.info if acted else logger.debug
        emit(
            RUN_RECOVERY_SWEEP_COMPLETE,
            trigger=trigger,
            plans_seen=report.plans_seen,
            resumed=report.resumed,
            recomputed=report.recomputed,
            requeued=report.requeued,
            rejudged=report.rejudged,
            failed=report.failed,
            skipped=report.skipped,
        )
        return report

    async def _reconcile_one(self, plan: Plan) -> RunRecoveryReport:
        """Decide and apply what *plan* needs.

        Returns:
            A one-plan report, summed by the caller.
        """
        plan_id = str(plan.id)
        if plan.status in AWAITING_HUMAN_STATUSES:
            logger.debug(
                RUN_RECOVERY_PLAN_SKIPPED,
                plan_id=plan_id,
                plan_status=plan.status.value,
                reason="awaiting-human",
            )
            return _one(skipped=1)
        if self._ledger.is_driving(plan_id):
            logger.debug(
                RUN_RECOVERY_PLAN_SKIPPED,
                plan_id=plan_id,
                plan_status=plan.status.value,
                reason="already-driving",
            )
            return _one(skipped=1)
        if plan.status in UNFILLED_STATUSES:
            failed = await self._fail_unfilled(plan)
            return _one(failed=1) if failed else _one(skipped=1)
        revived = await self._revive_rows(plan)
        requeued, rejudged = revived.requeued, revived.rejudged
        # A dispatched plan with nothing left to dispatch is not stranded: its
        # rows are finished, dead, or parked on somebody, and the answer is
        # whatever the rollup derives from them. Driving it anyway spends a
        # whole coordination pass to gate every wave out and change nothing,
        # every tick, for as long as the plan sits there.
        if plan.status in TAIL_STATUSES or not revived.dispatchable:
            # The tail stages key their work on an id derived from the plan
            # and read their own state, so one rollup pass re-drives whichever
            # stage the plan sits in without minting a second job.
            await self._recompute_plan(plan)
            logger.info(
                RUN_RECOVERY_PLAN_RESUMED,
                plan_id=plan_id,
                plan_status=plan.status.value,
                requeued=requeued,
                rejudged=rejudged,
                how="tail-recompute" if plan.status in TAIL_STATUSES else "recompute",
            )
            return _one(recomputed=1, requeued=requeued, rejudged=rejudged)
        if plan.status not in DRIVEN_STATUSES:
            # Reaching the driver by elimination is what makes an unnamed
            # status invisible: a member added later and put in none of these
            # sets would be driven silently, which is the "a status nothing
            # names is a status nothing watches" hole this module exists to
            # refuse. Naming the driven set turns that into a reported gap.
            logger.warning(
                RUN_RECOVERY_PLAN_SKIPPED,
                plan_id=plan_id,
                plan_status=plan.status.value,
                reason="status-classified-nowhere",
            )
            return _one(skipped=1)
        if not await self._drive_plan(plan):
            # The driver declined, so nothing is running. Reported as a skip
            # because that is what happened: counting it a resume told the
            # operator a plan whose objective task no longer exists was being
            # rescued on every pass, for ever, while nothing touched it. The
            # condition itself is named by the driver's own log line.
            logger.info(
                RUN_RECOVERY_PLAN_SKIPPED,
                plan_id=plan_id,
                plan_status=plan.status.value,
                reason="driver-declined",
            )
            return _one(skipped=1, requeued=requeued, rejudged=rejudged)
        logger.info(
            RUN_RECOVERY_PLAN_RESUMED,
            plan_id=plan_id,
            plan_status=plan.status.value,
            requeued=requeued,
            rejudged=rejudged,
            how="drive-waves",
        )
        return _one(resumed=1, requeued=requeued, rejudged=rejudged)

    async def _unfinished_plans(self) -> Sequence[Plan]:
        """Read every plan that has not reached a terminal status.

        Asked per unfinished status rather than by reading every row and
        discarding the terminal ones. The sweep runs on a cadence for the life
        of the deployment and terminal plans only accumulate, so a full scan
        makes each pass cost what the deployment has ever done rather than
        what it still owes. The filter is a single status, so the set is
        DERIVED from the enum minus the terminal ones: a member added later is
        swept because it is not terminal, which is the opposite default from
        a hand-listed set that would silently stop covering it.

        Returns:
            The unfinished plans, oldest page first within each status.
        """
        found: list[Plan] = []
        for status in sorted(set(PlanStatus) - TERMINAL_STATUSES):
            found.extend(await self._plans_with_status(status))
        return found

    async def _plans_with_status(self, status: PlanStatus) -> Sequence[Plan]:
        """Page through every plan currently at *status*.

        Args:
            status: The lifecycle status to enumerate.

        Returns:
            The matching plans, oldest page first.
        """
        spec = PlanFilterSpec(status=status)
        found: list[Plan] = []
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded by plan count
        while True:
            page = await self._persistence.plans.query(
                spec,
                limit=TASK_PAGE_SIZE,
                offset=offset,
            )
            found.extend(page)
            if len(page) < TASK_PAGE_SIZE:
                return found
            offset += TASK_PAGE_SIZE

    async def _revive_rows(self, plan: Plan) -> _RevivedRows:
        """Give *plan*'s stranded rows something watching them again.

        Two shapes, and they need opposite treatment. A row that was RUNNING
        is requeued, because its work never finished. A row IN REVIEW produced
        its work and is waiting on a verdict, so requeueing it would pay to
        redo finished work; what died is the session that was judging it, and
        asking the gates again is what replaces that.

        The rows are the plan's own work: the tasks implementing its items,
        and its assembly task. The objective task is deliberately left alone,
        because its status is derived from the items by the rollup and writing
        it here would be a second author of one value.

        Args:
            plan: The plan whose rows to revive.

        Returns:
            What was moved, and whether any row is left for a wave to run.
        """
        tasks = [
            task
            for task in await self._plan_tasks(plan)
            if task.plan_item_id is not None or is_integration_task(task, plan)
        ]
        if self._defers_to_queue:
            logger.debug(
                RUN_RECOVERY_PLAN_SKIPPED,
                plan_id=str(plan.id),
                reason="work-queue-owns-redelivery",
            )
            return _RevivedRows(
                requeued=0,
                rejudged=0,
                dispatchable=_has_work_to_dispatch(tasks),
            )
        moved = 0
        for task in tasks:
            if task.status in ORPHANED_TASK_STATUSES:
                moved += int(await self._requeue(task))
        rejudged = await self._rejudge_stranded_reviews(tasks)
        # Read from the rows as they were, plus what this pass just moved: a
        # requeue puts a row back in front of a wave, and a re-judged review
        # can send its work back, so either means there is something to run.
        return _RevivedRows(
            requeued=moved,
            rejudged=rejudged,
            dispatchable=_has_work_to_dispatch(tasks) or bool(moved or rejudged),
        )

    async def _rejudge_stranded_reviews(self, tasks: Sequence[Task]) -> int:
        """Ask the gates again for rows in review that nobody is judging.

        A row sits IN_REVIEW for two very different reasons, and only one of
        them is stranded. If an approval is open against it, a PERSON is being
        asked and the row is waiting correctly; re-running the gates there
        would decide something a human was asked about, and park a second
        approval beside the first. Otherwise the session that was judging it
        went with its process, and nothing watches IN_REVIEW.

        Args:
            tasks: The plan's own rows.

        Returns:
            How many reviews were asked for again.
        """
        rejudge = self._rejudge_task
        in_review = [task for task in tasks if task.status is TaskStatus.IN_REVIEW]
        if not in_review:
            return 0
        if rejudge is None or self._open_decisions is None:
            # Fails CLOSED on either half. Without a gate there is nothing to
            # judge with; without the decision reader there is no way to tell
            # a row waiting on a person from one waiting on nobody, and
            # re-judging the first decides something a human was asked about.
            logger.warning(
                RUN_RECOVERY_PLAN_SKIPPED,
                reason="no-review-gate" if rejudge is None else "no-decision-reader",
                in_review=len(in_review),
                note="rows left in review; nothing here may judge them",
            )
            return 0
        awaited_by_a_person = await self._open_decisions()
        asked = 0
        for task in in_review:
            if str(task.id) in awaited_by_a_person:
                logger.debug(
                    RUN_RECOVERY_PLAN_SKIPPED,
                    task_id=str(task.id),
                    reason="awaiting-human-decision",
                )
                continue
            await rejudge(task)
            asked += 1
            logger.info(
                RUN_RECOVERY_TASK_REQUEUED,
                task_id=str(task.id),
                plan_id=str(task.plan_id) if task.plan_id else None,
                from_status=task.status.value,
                how="rejudge",
            )
        return asked

    async def _plan_tasks(self, plan: Plan) -> Sequence[Task]:
        """Read every task filed against *plan*.

        Returns:
            The plan's tasks.
        """
        found: list[Task] = []
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded by plan size
        while True:
            page = await self._persistence.tasks.query(
                TaskFilterSpec(plan=plan.id),
                limit=TASK_PAGE_SIZE,
                offset=offset,
            )
            found.extend(page)
            if len(page) < TASK_PAGE_SIZE:
                return found
            offset += TASK_PAGE_SIZE

    async def _requeue(self, task: Task) -> bool:
        """Move one orphaned row to INTERRUPTED.

        ``INTERRUPTED`` rather than straight back to ``ASSIGNED`` because it
        is the status that says what happened, and the dispatcher takes it
        from there: the wave that picks the row up assigns it to whoever it
        routes to, which may not be the agent that was running it.

        Args:
            task: The stranded row.

        Returns:
            Whether the engine moved it.
        """
        result = await self._task_engine.submit(
            TransitionTaskMutation(
                request_id=uuid4().hex,
                requested_by=RECOVERY_ACTOR,
                task_id=str(task.id),
                target_status=TaskStatus.INTERRUPTED,
                reason=_REQUEUE_REASON,
            )
        )
        if not result.success:
            logger.warning(
                RUN_RECOVERY_SWEEP_FAILED,
                task_id=str(task.id),
                from_status=task.status.value,
                error=result.error or "mutation rejected with no error detail",
            )
            return False
        logger.info(
            RUN_RECOVERY_TASK_REQUEUED,
            task_id=str(task.id),
            plan_id=str(task.plan_id) if task.plan_id else None,
            from_status=task.status.value,
        )
        return True

    async def _fail_unfilled(self, plan: Plan) -> bool:
        """Fail a shell whose items nobody will ever write.

        Guarded on the version the sweep read, not written blind. The verdict
        is inferred from a row observed a moment ago, and the writer this
        judges absent is the one that would move it: an unversioned save
        commits the verdict over whatever the decomposition wrote in between,
        destroying a plan that was being filled. A conflict IS the proof the
        inference lacked, so it leaves the shell alone rather than retrying
        against a fresher version, which would only re-run the same reasoning
        on evidence that has already contradicted it.

        Returns:
            Whether the plan was failed.
        """
        failed = plan.model_copy(
            update={
                "status": PlanStatus.FAILED,
                "failure_reason": _UNFILLED_REASON,
            }
        )
        try:
            await self._persistence.plans.update(failed, expected_version=plan.version)
        except PersistenceVersionConflictError:
            logger.info(
                RUN_RECOVERY_PLAN_SKIPPED,
                plan_id=str(plan.id),
                plan_status=plan.status.value,
                reason="another-writer-moved-it",
            )
            return False
        logger.warning(
            RUN_RECOVERY_PLAN_FAILED,
            plan_id=str(plan.id),
            reason="decomposition-did-not-survive-restart",
        )
        return True


def _has_work_to_dispatch(tasks: Sequence[Task]) -> bool:
    """Whether driving *tasks*' plan could still dispatch anything.

    Two ways it can. A row that still awaits dispatch is one a wave would
    run, asked through the coordination gate's own rule rather than a second
    list here, since both would be answering the same question. And NO rows
    at all means the dispatch stopped before it wrote the tree: the plan's
    work is not finished, it was never filed, and the drive is what files it.

    Returns:
        Whether a drive has something to do.
    """
    return not tasks or any(awaits_dispatch(task.status) for task in tasks)


def _one(
    *,
    resumed: int = 0,
    recomputed: int = 0,
    requeued: int = 0,
    rejudged: int = 0,
    failed: int = 0,
    skipped: int = 0,
) -> RunRecoveryReport:
    """Build a single-plan report.

    Returns:
        The report for one plan, with ``plans_seen`` at one.
    """
    return RunRecoveryReport(
        plans_seen=1,
        resumed=resumed,
        recomputed=recomputed,
        requeued=requeued,
        rejudged=rejudged,
        failed=failed,
        skipped=skipped,
    )


__all__ = [
    "AWAITING_HUMAN_STATUSES",
    "DRIVEN_STATUSES",
    "ORPHANED_TASK_STATUSES",
    "RECOVERY_ACTOR",
    "UNFILLED_STATUSES",
    "PlanDriver",
    "RunRecoveryReconciler",
    "RunRecoveryReport",
]
