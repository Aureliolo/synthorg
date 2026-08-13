"""The graph writes a decided plan-approval makes on its way out.

Every one of them runs after the decision is already durable on the
approval, so none may propagate: a raise here would make a retried request
re-run the whole resume against a decision that already stands. They are
kept together and apart from the dispatch flow because they share exactly
that contract, and because the dispatch reads better as the sequence of
things it decides rather than the sequence of things it writes.
"""

from typing import Final

from synthorg.api.services.plan_service import PlanService
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ResourceNotFoundError, VersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import task_engine_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
    APPROVAL_GATE_PLAN_TASK_TRANSITION_FAILED,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

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
            target_status=target.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-decision task transition failed; status may diverge",
        )


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
) -> None:
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
    """
    if not plan_id:
        return
    service = build_plan_service(persistence_of(app_state), clock=app_state.clock)
    if not await _plan_exists_for_sync(service, plan_id, status):
        return

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
            target_status=status.value,
            note="plan-status sync skipped: durable plan deleted mid-sync",
        )
    except VersionConflictError:
        logger.error(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            attempts=_MAX_STATUS_SYNC_ATTEMPTS,
            note="plan-status sync lost repeated version conflicts; "
            "/plans status diverges from the recorded decision",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_PLAN_STATUS_SYNC_FAILED,
            plan_id=plan_id,
            target_status=status.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan-status sync failed; /plans status may lag the decision",
        )
