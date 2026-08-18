# module-kind: code
"""Boot wiring for the run-recovery sweep, and the driver it resumes through.

Two things live here because they are one decision: what it means to "resume"
a plan is to hand it to the same coordinator an approval hands it to, and the
coordinator is assembled in this layer. The sweep itself reads only the graph,
so it takes the driver as a port and stays testable without an app.

The boot pass runs before the cadence starts. That ordering is the point of
the whole subsystem: a restart is when runs are stranded, so the first pass
must not wait out an interval before anybody asks.
"""

import asyncio
from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.coordination.run_ledger import LiveRunLedger
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.plan_mapping import decomposition_from_plan
from synthorg.engine.review.factory import build_review_pipeline
from synthorg.engine.review_staffing.rejudge import rejudge_released_task
from synthorg.engine.run_recovery.reconciler import (
    RECOVERY_ACTOR,
    RunRecoveryReconciler,
)
from synthorg.engine.run_recovery.scheduler import (
    DEFAULT_RESYNC_INTERVAL_SECONDS,
    RunRecoveryScheduler,
)
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.run_recovery import (
    RUN_RECOVERY_PLAN_FAILED,
    RUN_RECOVERY_PLAN_RESUMED,
    RUN_RECOVERY_PLAN_SKIPPED,
)
from synthorg.persistence.state import PersistenceStateSlice, persistence_of
from synthorg.settings.state import SettingsStateSlice
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)

_BOOT_TRIGGER: str = "boot"


def live_run_ledger_of(app_state: AppState) -> LiveRunLedger:
    """Return the process's live-run ledger, creating it on first ask.

    Created lazily rather than at construction because both callers that need
    it (the approval path and the recovery sweep) run long after the slice is
    first built, and a second ledger would defeat the one thing it does.

    Returns:
        The one ledger for this application state.
    """
    ledger = app_state.slice(EngineStateSlice).live_run_ledger
    if ledger is None:
        ledger = LiveRunLedger()
        app_state.wire(EngineStateSlice, live_run_ledger=ledger)
    return ledger


async def drive_plan_waves(app_state: AppState, plan: Plan) -> None:
    """Hand *plan*'s remaining waves back to the coordinator.

    Returns as soon as the drive is under way: resuming a plan runs agents,
    which takes minutes, and the sweep has other stranded plans to reach.

    Args:
        app_state: Application state holding the coordinator and the graph.
        plan: The plan to resume.
    """
    ledger = live_run_ledger_of(app_state)
    plan_id = str(plan.id)
    if not ledger.try_claim(plan_id):
        return
    started = False
    try:
        started = await _start_drive(app_state, plan)
    finally:
        if not started:
            ledger.release(plan_id)


async def _file_missing_children(
    app_state: AppState,
    children: Sequence[Task],
) -> None:
    """File the plan's child rows that do not exist yet, and only those.

    The dispatch path files the whole tree before the first wave runs, so a
    process that stopped between approving a plan and finishing that write
    leaves a plan with no work queryable at all: nothing to dispatch, nothing
    to derive a status from, and no route back.

    Only the ABSENT rows are written. The ids are derived from the plan items,
    so re-saving an existing one would be accepted and would reset the status
    of every subtask that had already finished, which would undo the run this
    is trying to rescue.

    Args:
        app_state: Application state carrying the persistence backend.
        children: The tasks rebuilt from the plan's work items.
    """
    tasks = persistence_of(app_state).tasks
    missing = [child for child in children if await tasks.get(str(child.id)) is None]
    if not missing:
        return
    await tasks.save_many(tuple(missing))
    logger.info(
        RUN_RECOVERY_PLAN_RESUMED,
        note="filed child rows a stopped dispatch never wrote",
        child_count=len(missing),
    )


async def _start_drive(app_state: AppState, plan: Plan) -> bool:
    """Build the dispatch and spawn it.

    Returns:
        Whether a background drive now owns the plan's ledger claim.
    """
    plan_id = str(plan.id)
    coordinator = app_state.slice(RuntimeStateSlice).coordinator
    if coordinator is None:
        logger.warning(
            RUN_RECOVERY_PLAN_SKIPPED,
            plan_id=plan_id,
            reason="no-coordinator",
        )
        return False
    persistence = persistence_of(app_state)
    task = await persistence.tasks.get(str(plan.parent_task_id))
    if task is None:
        logger.warning(
            RUN_RECOVERY_PLAN_SKIPPED,
            plan_id=plan_id,
            reason="objective-task-missing",
        )
        return False
    # Rebuilt from the plan's own items, exactly as approval does, so a
    # resumed run dispatches the plan an operator approved rather than
    # whatever an earlier process happened to be holding. The rows already
    # exist and are NOT re-filed: writing them again would reset the status of
    # every subtask that had already finished.
    decomposition = decomposition_from_plan(plan, parent_task=task)
    await _file_missing_children(app_state, decomposition.created_tasks)
    agents = await agent_registry_of(app_state).list_active()
    background = asyncio.create_task(
        _run_drive(
            app_state,
            coordinator_context=CoordinationContext(
                task=task,
                available_agents=agents,
                # The rollup owns the objective task's status, here as
                # everywhere else; this run is one sweep over one plan.
                plan_id=NotBlankStr(plan_id),
            ),
            plan=plan,
            decomposition=decomposition,
        )
    )
    background.add_done_callback(
        log_task_exceptions(logger, RUN_RECOVERY_PLAN_FAILED, plan_id=plan_id),
    )
    app_state.plan_dispatch_background_tasks.add(background)
    background.add_done_callback(app_state.plan_dispatch_background_tasks.discard)
    return True


async def _run_drive(
    app_state: AppState,
    *,
    coordinator_context: CoordinationContext,
    plan: Plan,
    decomposition: DecompositionResult,
) -> None:
    """Run the resumed waves, then let the rollup read what happened.

    Raises:
        CancelledError: Re-raised unchanged. A cancelled resume leaves the
            plan exactly as it was, which is correct: the next boot pass
            finds it again and resumes it again. Failing it here would turn
            the one event this subsystem exists to survive into a dead plan.
        MemoryError: Re-raised uncaught so a genuine OOM is never masked.
        RecursionError: Re-raised uncaught alongside ``MemoryError``.
    """
    plan_id = str(plan.id)
    ledger = live_run_ledger_of(app_state)
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    coordinator = app_state.slice(RuntimeStateSlice).coordinator
    try:
        if coordinator is None:
            return
        await coordinator.coordinate(
            coordinator_context,
            precomputed_plan=decomposition,
        )
        logger.info(
            RUN_RECOVERY_PLAN_RESUMED,
            plan_id=plan_id,
            note="resumed waves finished",
        )
    except asyncio.CancelledError:
        logger.info(
            RUN_RECOVERY_PLAN_SKIPPED,
            plan_id=plan_id,
            reason="cancelled-mid-resume",
            note="left resumable; the next recovery pass picks it up",
        )
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- surface, never strand the plan
        reraise_critical(exc)
        logger.warning(
            RUN_RECOVERY_PLAN_FAILED,
            plan_id=plan_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    finally:
        # Released before the rollup so a recompute that re-enters this path
        # is not refused by a claim its own caller still holds.
        ledger.release(plan_id)
        if rollup is not None:
            # However the waves ended, the rollup is the one authority on what
            # it meant: it re-derives the plan from its items and routes a
            # stall to the replan trigger. Without this a resumed run that
            # delivered nothing would sit EXECUTING again, which is the state
            # recovery exists to leave behind.
            await rollup.recompute(plan.id)


async def _tasks_awaiting_a_person(
    approvals: ApprovalStoreProtocol,
) -> frozenset[str]:
    """Read which tasks currently have a decision open against them.

    Returns:
        The task ids somebody is being asked about right now.
    """
    pending = await approvals.list_items(status=ApprovalStatus.PENDING)
    return frozenset(item.task_id for item in pending if item.task_id)


async def wire_run_recovery(app_state: AppState) -> None:
    """Run the boot recovery pass, then start its cadence.

    Idempotent for re-entered lifespans: returns early when a scheduler is
    already published.

    Raises:
        SubsystemDeclinedError: When a collaborator the sweep reads or writes
            through is absent, named so the status surface can report which.
    """
    engine_slice = app_state.slice(EngineStateSlice)
    if engine_slice.run_recovery_scheduler is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        msg = "no persistence backend; stranded runs are read from it"
        raise SubsystemDeclinedError(msg)
    if engine_slice.task_engine is None:
        msg = "no task engine; requeueing a stranded row is a validated hop"
        raise SubsystemDeclinedError(msg)
    if app_state.slice(HrStateSlice).agent_registry is None:
        msg = "no agent registry; a resumed wave has nobody to route work to"
        raise SubsystemDeclinedError(msg)
    rollup = engine_slice.project_rollup_service
    if rollup is None:
        msg = (
            "no project rollup; a resumed plan would have nothing to read "
            "what its waves delivered, so it could not conclude"
        )
        raise SubsystemDeclinedError(msg)

    review_gate = app_state.slice(ApprovalStateSlice).review_gate
    approvals = app_state.slice(ApprovalStateSlice).store
    task_engine = engine_slice.task_engine
    reconciler = RunRecoveryReconciler(
        persistence=persistence_of(app_state),
        task_engine=task_engine,
        ledger=live_run_ledger_of(app_state),
        drive_plan=lambda plan: drive_plan_waves(app_state, plan),
        recompute_plan=lambda plan: rollup.recompute(plan.id),
        # Built here rather than read from the auto-review wiring, and for the
        # same reason the staffing sweep builds its own: a row in review
        # already HAD its review start, and the session running it went with
        # its process. Asking again is resuming that review, not starting an
        # autonomous one the operator did not ask for, and the gate still
        # escalates wherever its own rules say.
        rejudge_task=(
            None
            if review_gate is None
            else lambda task: rejudge_released_task(
                task,
                review_gate=review_gate,
                review_pipeline=build_review_pipeline(),
                task_engine=task_engine,
                actor=RECOVERY_ACTOR,
            )
        ),
        open_decisions=(
            None if approvals is None else lambda: _tasks_awaiting_a_person(approvals)
        ),
        # A deployment running distributed workers hands execution to the work
        # queue, whose redelivery of an unacknowledged claim already owns
        # recovering a dead runner. Requeueing here as well would be a second
        # answer, and the row it moved could be one a live worker is running.
        defers_to_queue=(
            app_state.slice(RuntimeStateSlice).distributed_task_queue is not None
        ),
    )
    # Before the cadence starts, because a restart is exactly when runs are
    # stranded and waiting out an interval first would leave the board showing
    # work in flight with nothing behind it for that whole interval.
    await reconciler.reconcile(trigger=_BOOT_TRIGGER)
    scheduler = RunRecoveryScheduler(
        reconciler,
        interval_seconds=DEFAULT_RESYNC_INTERVAL_SECONDS,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    await scheduler.start()
    try:
        app_state.wire(EngineStateSlice, run_recovery_scheduler=scheduler)
    except Exception:
        await scheduler.stop()
        raise
    logger.info(API_APP_STARTUP, service="run_recovery", note="wired")


async def unwire_run_recovery(app_state: AppState) -> None:
    """Stop the sweep and drop it from the slice."""
    scheduler = app_state.slice(EngineStateSlice).run_recovery_scheduler
    if scheduler is None:
        return
    try:
        await scheduler.stop()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # The slice still drops the scheduler: leaving a stopped-or-not one
        # published would report the sweep up while a rebuild waits on it.
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="run_recovery",
            note="sweep scheduler stop failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    engine_slice = app_state.slice(EngineStateSlice)
    app_state.swap_slice(
        engine_slice.model_copy(update={"run_recovery_scheduler": None})
    )
    logger.info(API_APP_STARTUP, service="run_recovery", note="unwired")


__all__ = [
    "drive_plan_waves",
    "live_run_ledger_of",
    "unwire_run_recovery",
    "wire_run_recovery",
]
