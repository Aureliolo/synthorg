# module-kind: code
"""Boot wiring for the review-staffing sweep.

Assembles the reconciler over the task engine, the roster and the hiring
pipeline, then starts its cadence. Started before the slice is published and
rolled back on failure, so a scheduler that could not start never reads as
wired.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.review.factory import build_review_pipeline
from synthorg.engine.review_staffing_reconciler import ReviewStaffingReconciler
from synthorg.engine.review_staffing_scheduler import (
    DEFAULT_RESYNC_INTERVAL_SECONDS,
    ReviewStaffingScheduler,
)
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.role_staffing import RoleStaffingService
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import PersistenceStateSlice, persistence_of
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


async def wire_review_staffing(app_state: AppState) -> None:
    """Build and start the staffing sweep.

    Idempotent for re-entered lifespans: returns early when a scheduler is
    already published.

    Raises:
        SubsystemDeclinedError: When a collaborator the sweep reads or writes
            through is absent, named so the status surface can report which.
    """
    engine_slice = app_state.slice(EngineStateSlice)
    if engine_slice.review_staffing_scheduler is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        msg = "no persistence backend; the parked backlog is read from it"
        raise SubsystemDeclinedError(msg)
    if engine_slice.task_engine is None:
        msg = "no task engine; releasing a park is a validated status hop"
        raise SubsystemDeclinedError(msg)
    if app_state.slice(HrStateSlice).agent_registry is None:
        msg = "no agent registry; the sweep asks it who holds the gate roles"
        raise SubsystemDeclinedError(msg)
    review_gate = app_state.slice(ApprovalStateSlice).review_gate
    if review_gate is None:
        msg = (
            "no review gate; releasing a park without re-running the gates "
            "would move work somewhere nothing judges it"
        )
        raise SubsystemDeclinedError(msg)

    resolver = app_state.slice(SettingsStateSlice).config_resolver
    persistence = persistence_of(app_state)
    registry = agent_registry_of(app_state)
    scheduler = ReviewStaffingScheduler(
        ReviewStaffingReconciler(
            task_repo=persistence.tasks,
            task_engine=engine_slice.task_engine,
            staffing=RoleStaffingService(registry=registry),
            review_gate=review_gate,
            # Built here rather than read from the auto-review wiring, and
            # deliberately not gated on ``engine.auto_review_on_completion``:
            # a parked task already HAD its review start, and the gate found
            # nobody to run it. Re-running is resuming that review, not
            # starting an autonomous one the operator did not ask for, and
            # the gate still escalates to a human wherever its own rules say.
            review_pipeline=build_review_pipeline(),
            project_repo=persistence.projects,
            # Optional by design: a boot with no approval store has no hiring
            # pipeline, and the sweep still releases what it can and still
            # names what is missing. It just cannot ask for anybody.
            hiring=lambda: app_state.slice(HrStateSlice).hiring_service,
            # Read live, never captured: boot replaces the dispatcher after
            # the subsystems come up and closes the one that was current, so
            # a captured instance is shut by the first unstaffed role.
            notifications=lambda: app_state.slice(NotificationsStateSlice).dispatcher,
        ),
        interval_seconds=DEFAULT_RESYNC_INTERVAL_SECONDS,
        config_resolver=resolver,
    )
    # A scheduler that cannot start is an activation failure, not a decline:
    # letting it through would publish nothing and leave the reconciler
    # reporting a condition it never declared.
    await scheduler.start()
    # A role being filled is the event the sweep exists to react to, and the
    # roster is where it happens, whether by a dashboard edit, an approved
    # hire, or a config load. The nudge only shortens the wait; the cadence
    # stays the guarantee, so a route that never notifies costs one tick.
    registry.set_roster_change_listener(scheduler.nudge)
    app_state.wire(EngineStateSlice, review_staffing_scheduler=scheduler)
    logger.info(API_APP_STARTUP, service="review_staffing", note="wired")


async def unwire_review_staffing(app_state: AppState) -> None:
    """Stop the sweep and drop it from the slice."""
    scheduler = app_state.slice(EngineStateSlice).review_staffing_scheduler
    if scheduler is None:
        return
    # Cleared first: a listener pointing at a stopped scheduler would keep
    # firing into a wait nothing is left to shorten.
    if app_state.slice(HrStateSlice).agent_registry is not None:
        agent_registry_of(app_state).set_roster_change_listener(None)
    try:
        await scheduler.stop()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # The slice still drops the scheduler: leaving a stopped-or-not one
        # published would report the sweep up while a rebuild waits on it.
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="review_staffing",
            note="sweep scheduler stop failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    engine_slice = app_state.slice(EngineStateSlice)
    app_state.swap_slice(
        engine_slice.model_copy(update={"review_staffing_scheduler": None})
    )
    logger.info(API_APP_STARTUP, service="review_staffing", note="unwired")


__all__ = ["unwire_review_staffing", "wire_review_staffing"]
