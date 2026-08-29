"""The graph writes a decided plan-approval makes on its way out.

Every one of them runs after the decision is already durable on the
approval, so none may propagate: a raise here would make a retried request
re-run the whole resume against a decision that already stands. They are
kept together and apart from the dispatch flow because they share exactly
that contract, and because the dispatch reads better as the sequence of
things it decides rather than the sequence of things it writes.
"""

from typing import Final
from uuid import UUID

from synthorg.api.controllers._task_teardown import terminate_task
from synthorg.api.services.plan_service import PlanService
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ResourceNotFoundError, VersionConflictError
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import task_engine_of
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_PLAN_DISPATCH_FAILED,
    APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
    APPROVAL_GATE_PLAN_TASK_TRANSITION_FAILED,
)
from synthorg.persistence.state import persistence_of
from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)

#: Who a dispatch-path transition is attributed to. Mirrors the dispatch
#: module's own actor, so the teardown a failed dispatch performs is
#: attributable to the same party as the dispatch that filed the rows.
_DISPATCH_ACTOR: Final[str] = "plan-dispatch"

# Bounded compare-and-swap retries when the durable plan is reworked concurrently
# with its approval sync, so a losing status write re-reads and reapplies rather
# than leaving the plan's status permanently diverged from the recorded decision.
_MAX_STATUS_SYNC_ATTEMPTS: Final[int] = 3


async def mark_task(
    app_state: AppState,
    task_id: str | None,
    actor: str,
    *,
    target: TaskStatus,
    reason: str,
) -> None:
    """Transition the plan's parent task, surfacing a failure at ERROR.

    A failure here means the approval decision and the task's real status
    diverge (a rejected plan whose task stays live, or a failed dispatch whose
    task never reached FAILED), which is operationally meaningful: log ERROR so
    the divergence is visible, but do not raise (the decision is already
    persisted; a 5xx here would mislabel an already-recorded decision).

    *actor* is the operator on a cancellation they asked for and the dispatcher
    on a failure they did not.

    Args:
        app_state: Application state carrying the task engine.
        task_id: The parent task, or ``None`` when the approval named none.
        actor: Who the transition is attributed to.
        target: The status to move the task to.
        reason: The reason recorded on the transition.
    """
    if task_id is None:
        return
    try:
        await task_engine_of(app_state).transition_task(
            task_id,
            target,
            requested_by=actor,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            APPROVAL_GATE_PLAN_TASK_TRANSITION_FAILED,
            task_id=task_id,
            actor=actor,
            target_status=target.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-decision task transition failed; status may diverge",
        )


async def abandon_filed_children(
    app_state: AppState,
    plan_id: str | None,
    *,
    parent_task_id: str | None,
    why: str,
) -> None:
    """Terminate the child rows a failed dispatch already filed.

    The dispatch files the whole task tree BEFORE it hands the plan to the
    coordinator, so a dispatch that raises before the wave loop is entered
    leaves those rows at CREATED. Failing the parent and the plan does not
    reach them, and the plan's own FAILED status is terminal, so neither the
    recovery sweep nor the rollup ever looks at that plan again: the rows sit
    at CREATED for ever, under a plan the board shows as closed, with nothing
    watching them and no exit.

    The wave loop parks what it drops, but only what it dropped: a raise from
    ``build_execution_waves`` (a dependency cycle in an operator-edited item
    graph, a routing decision naming no created task) happens BEFORE the loop
    is entered, so none of its parking runs. This is the same teardown the
    supersede path already does for a retired plan's work, applied to the
    other way a plan stops owning the rows it filed.

    The parent is skipped by id: the caller has just moved it to FAILED
    deliberately, which is NOT truly terminal (the engine may retry a failed
    task, and FAILED -> ASSIGNED is what keeps the plan re-runnable), so
    terminating it here would undo that choice.

    Never propagates, like every write in this module: the decision is already
    durable, and a raise would make a retried request re-run the resume.

    Args:
        app_state: Application state carrying persistence and the engine.
        plan_id: The plan whose filed work is being abandoned.
        parent_task_id: The objective task the caller already failed.
        why: Recorded on each transition, so the board says what stopped it.
    """
    if plan_id is None:
        return
    try:
        doomed = await _filed_children(app_state, plan_id)
        abandoned, refused = await _terminate_children(
            task_engine_of(app_state),
            doomed,
            parent_task_id=parent_task_id,
            why=why,
        )
        if abandoned:
            logger.info(
                APPROVAL_GATE_PLAN_DISPATCH_FAILED,
                plan_id=plan_id,
                abandoned=abandoned,
                note="terminated the child rows the failed dispatch had filed",
            )
        if refused:
            # Counted and reported separately, because a refusal IS the harm
            # this function exists to prevent: those rows keep a status
            # nothing watches under a plan about to go terminal, and a
            # success-only log would leave the one outcome worth acting on
            # as silence.
            logger.error(
                APPROVAL_GATE_PLAN_DISPATCH_FAILED,
                plan_id=plan_id,
                refused=refused,
                note="child rows refused termination and stay unwatched under "
                "a terminal plan",
            )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            APPROVAL_GATE_PLAN_DISPATCH_FAILED,
            plan_id=plan_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="could not abandon filed children; rows may sit at CREATED",
        )


async def _terminate_children(
    engine: TaskEngine,
    doomed: list[Task],
    *,
    parent_task_id: str | None,
    why: str,
) -> tuple[int, int]:
    """Cancel each child that still has somewhere to go.

    Args:
        engine: The engine that owns task transitions.
        doomed: Every child row filed under the plan.
        parent_task_id: The objective task, skipped by id because the caller
            has just moved it to FAILED deliberately and FAILED is not truly
            terminal, so terminating it here would undo that choice.
        why: Recorded on each transition.

    Returns:
        ``(abandoned, refused)``: how many reached a terminal, and how many
        the engine would not move.
    """
    abandoned = 0
    refused = 0
    for task in doomed:
        if str(task.id) == parent_task_id:
            continue
        if task.status in TRULY_TERMINAL_STATUSES:
            continue
        reached = await terminate_task(
            engine,
            task,
            requested_by=_DISPATCH_ACTOR,
            reason=f"dispatch failed before this work could run: {why}",
        )
        if reached is not None:
            abandoned += 1
        else:
            refused += 1
    return abandoned, refused


async def fail_plan(app_state: AppState, plan_id: str | None, why: str) -> None:
    """Drive a plan that cannot dispatch out of its dispatch status.

    Without this the plan rests in APPROVED or EXECUTING with a failed parent:
    a state that can be entered, has no exit, and that nothing watches. FAILED
    is terminal, carries the reason on the plan for Plan Review to show, and is
    reachable from both dispatch statuses.

    Args:
        app_state: Application state carrying the plan service.
        plan_id: The plan to fail, or ``None`` when the approval named none.
        why: The reason recorded on the plan.
    """
    await sync_plan_status(
        app_state,
        plan_id,
        PlanStatus.FAILED,
        requested_by=_DISPATCH_ACTOR,
        failure_reason=NotBlankStr(why),
    )


async def record_dispatch_failure(
    app_state: AppState,
    exc: Exception,
    *,
    approval_id: str,
    task_id: str | None,
    plan_id: str | None,
) -> None:
    """Settle the graph for a dispatch that raised.

    Shared by both halves of the dispatch, which fail the same way and must
    say so identically whether the request was still open or not.

    Args:
        app_state: Application state carrying the graph.
        exc: What the dispatch raised.
        approval_id: The approval whose dispatch failed.
        task_id: The objective task.
        plan_id: The plan being dispatched.
    """
    log_exception_redacted(
        logger,
        APPROVAL_GATE_PLAN_DISPATCH_FAILED,
        exc,
        approval_id=approval_id,
        note="approved plan could not be resumed; failing task and plan",
    )
    await _settle_failed_dispatch(
        app_state,
        task_id=task_id,
        plan_id=plan_id,
        task_reason="approved plan could not be resumed",
        why=safe_error_description(exc),
    )


async def fail_dispatch(
    app_state: AppState,
    approval_id: str,
    *,
    task_id: str | None,
    plan_id: str | None,
    why: str,
) -> None:
    """Settle the graph for a dispatch precondition that was not met.

    The approval is already persisted APPROVED, so a swallowed failure would
    leave the parent silently stuck in its pre-approval status with no
    board-visible signal.

    Args:
        app_state: Application state carrying the graph.
        approval_id: The approval whose dispatch failed.
        task_id: The objective task.
        plan_id: The plan being dispatched.
        why: What stopped the dispatch.
    """
    logger.error(
        APPROVAL_GATE_PLAN_DISPATCH_FAILED,
        approval_id=approval_id,
        note="approved plan cannot dispatch",
        why=why,
    )
    await _settle_failed_dispatch(
        app_state,
        task_id=task_id,
        plan_id=plan_id,
        task_reason=f"approved plan could not be resumed: {why}",
        why=why,
    )


async def _settle_failed_dispatch(
    app_state: AppState,
    *,
    task_id: str | None,
    plan_id: str | None,
    task_reason: str,
    why: str,
) -> None:
    """Move every row a failed dispatch owns to a status something watches.

    The order is load-bearing. The children go BEFORE the plan, because FAILED
    is terminal and both the recovery sweep and the rollup skip a terminal
    plan: anything still holding those rows once the plan is closed holds them
    for ever.

    Args:
        app_state: Application state carrying the graph.
        task_id: The objective task, failed rather than cancelled so the board
            shows the initiative failed and it stays re-runnable.
        plan_id: The plan being dispatched.
        task_reason: The reason recorded on the objective task.
        why: The reason recorded on the plan and on each abandoned child.
    """
    await mark_task(
        app_state,
        task_id,
        _DISPATCH_ACTOR,
        target=TaskStatus.FAILED,
        reason=task_reason,
    )
    await abandon_filed_children(app_state, plan_id, parent_task_id=task_id, why=why)
    await fail_plan(app_state, plan_id, f"dispatch failed: {why}")


async def _filed_children(app_state: AppState, plan_id: str) -> list[Task]:
    """Read every task filed against *plan_id*.

    Drained fully before anything is terminated, for the reason the supersede
    path drains first: cancelling while paging mutates the rows the offset
    walks over.

    Args:
        app_state: Application state carrying persistence.
        plan_id: The plan whose tasks to read.

    Returns:
        The tasks, in repository order.
    """
    tasks = persistence_of(app_state).tasks
    found: list[Task] = []
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded by plan item count
    while True:
        page = await tasks.query(
            TaskFilterSpec(plan=UUID(plan_id)),
            limit=DEFAULT_PAGE_SIZE,
            offset=offset,
        )
        found.extend(page)
        if len(page) < DEFAULT_PAGE_SIZE:
            return found
        offset += DEFAULT_PAGE_SIZE


async def _plan_exists_for_sync(
    service: PlanService, plan_id: str, status: PlanStatus
) -> bool:
    """Whether the plan is there to sync, reporting why when it is not.

    Both answers are the same outcome for the caller (nothing to write) and
    neither may propagate: the decision is already persisted on the approval,
    so raising would make a retried request re-run the whole resume. Split
    out because it is a lookup and the CAS loop below is a write, and one
    function doing both left the retry the reader is looking for behind two
    unrelated failure branches.

    Args:
        service: The plan service to read through.
        plan_id: The plan the decision named.
        status: The status the sync targets, for the log line.

    Returns:
        ``True`` when the plan was read; ``False`` when it is missing or the
        lookup failed, both already logged.
    """
    try:
        initial = await service.get(plan_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-status sync failed during initial lookup",
        )
        return False
    if initial is None:
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            note="plan-status sync skipped: durable plan not found",
        )
        return False
    return True


async def sync_plan_status(
    app_state: AppState,
    plan_id: str | None,
    status: PlanStatus,
    *,
    requested_by: str | None = None,
    failure_reason: NotBlankStr | None = None,
) -> bool:
    """Reflect an approval decision onto the durable plan's status.

    Routed through :class:`PlanService` so the decision transition gets the
    same ``API_PLAN_*`` audit coverage as an operator edit. The decision is
    already persisted on the approval, so a failure here is logged, not raised.
    A concurrent rework (version conflict) is retried after a re-read, up to
    ``_MAX_STATUS_SYNC_ATTEMPTS``, so a lost CAS write does not leave the
    ``/plans`` status permanently diverged from the recorded decision; a
    persistent conflict is escalated to ERROR (the divergence is not transient).

    Args:
        app_state: Application state carrying persistence and the clock.
        plan_id: The plan the decision named, if it named one.
        status: The status to reflect onto it.
        requested_by: Who the transition is attributed to.
        failure_reason: The reason carried on the plan for a failure status.

    Returns:
        Whether the plan is known to carry *status* now. Reported rather than
        swallowed because one caller stages an initiative with it: a lost write
        there leaves the plan at APPROVED while the whole task tree is filed
        behind it, and a caller that cannot see the loss reports success for a
        plan that never entered the contract stage.
    """
    if not plan_id:
        return False
    service = build_plan_service(persistence_of(app_state), clock=app_state.clock)
    if not await _plan_exists_for_sync(service, plan_id, status):
        return False

    async def read() -> tuple[Plan, int]:
        # Re-read on each attempt so a retry's CAS uses the current version. A
        # plan deleted after the initial fetch aborts the loop cleanly: a write
        # against the stale last-known plan would only spin the CAS retries into
        # a misleading "version conflict" before failing anyway.
        plan = await service.get(plan_id)
        if plan is None:
            msg = "durable plan deleted mid status-sync"
            raise ResourceNotFoundError(msg)
        return plan, plan.version

    async def write(plan: Plan, _version: int) -> None:
        await service.sync_status(
            plan,
            status,
            requested_by=requested_by,
            failure_reason=failure_reason,
        )

    try:
        await CASRetryHandler(
            resource="plan_status_sync", max_attempts=_MAX_STATUS_SYNC_ATTEMPTS
        ).execute(read, write)
    except ResourceNotFoundError:
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            requested_by=requested_by,
            target_status=status.value,
            note="plan-status sync skipped: durable plan deleted mid-sync",
        )
        return False
    except VersionConflictError:
        logger.error(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            requested_by=requested_by,
            target_status=status.value,
            attempts=_MAX_STATUS_SYNC_ATTEMPTS,
            note="plan-status sync lost repeated version conflicts; "
            "/plans status diverges from the recorded decision",
        )
        return False
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            requested_by=requested_by,
            target_status=status.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-status sync failed; /plans status may lag the decision",
        )
        return False
    return True
