# module-kind: orchestrator
"""Startup wiring for the sprint recovery sweep.

Its own subsystem rather than a step inside the sprint service's wiring,
because it is its own thing to start and its own thing to stop. Folded in
there, its start sat between registering the completion observer and
publishing the service, so a failure to start it left the observer attached
to a service the slice never received: a live, unreachable listener that the
next reconcile pass then duplicated, since the re-entry guard reads the
slice. Separated, the service's wiring ends where it did before this sweep
existed, and the sweep declines on its own with its own reason.
"""

import asyncio
from typing import Final

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.sprint_recovery import SprintRecoveryReconciler
from synthorg.engine.workflow.sprint_tail_scheduler import (
    BOOT_TRIGGER,
    SprintTailScheduler,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.workflow import SPRINT_TAIL_SWEEP_PAUSED
from synthorg.persistence.sprint_factory import build_sprint_repository
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)

#: Hard ceiling on the boot pass, independent of the configured cadence.
#: The pass is awaited inline in the lifespan, so its bound decides how long
#: startup can hang; the cadence is an operator setting that legitimately
#: reaches a full day, which would make "bounded" mean nothing here.
_BOOT_PASS_CEILING_SECONDS: Final[float] = 30.0


async def _run_boot_pass(
    reconciler: SprintRecoveryReconciler,
    *,
    config_resolver: ConfigResolverProtocol,
    interval: float,
) -> None:
    """Run the one recovery pass a restart owes, unless the sweep is paused.

    Before the cadence starts, because a restart is when sprints are
    stranded and waiting out an interval first would leave the board
    showing work in flight with nothing behind it for that whole interval.

    Args:
        reconciler: The sweep to run once.
        config_resolver: Reads the pause switch, live.
        interval: The configured cadence, the other half of the bound.

    Raises:
        TimeoutError: When the pass raises one that is not this function's
            own deadline, which is the only timeout it waives.
    """
    # Whether the sweep may run is ONE decision, and the scheduler already
    # owns it for every later pass. Asked only there, the answer at boot was
    # an unconditional yes, so an operator who paused recovery got it back on
    # the next restart -- which is when the sweep has the most to move, and a
    # kill switch a deploy defeats is worse than none at all. Namespace and
    # key are spelled out rather than borrowed, because the liveness gate
    # reads a call site textually and a setting reached through an
    # indirection it cannot follow reads as one nothing consumes.
    paused = await resolve_bool_with_fallback(
        resolver=config_resolver,
        namespace="engine",
        key="sprint_tail_sweep_paused",
        fallback=False,
    )
    if paused:
        # The scheduler still starts: pausing stops the sweep from running,
        # not from being there to unpause.
        logger.info(SPRINT_TAIL_SWEEP_PAUSED, trigger=BOOT_TRIGGER)
        return
    # Bounded, because this one is awaited inline in the lifespan: the pass
    # reads every unfinished sprint, and a persistence backend that hangs
    # would hold startup open with no ceiling, so the readiness probe never
    # succeeds and the orchestrator restarts into the same wait. What a
    # timed-out pass did not reach is exactly what the next pass covers, so
    # the interval is an upper bound worth taking when it is the smaller of
    # the two; the startup ceiling is what stops the bound BEING the
    # configured cadence, which an operator may legitimately set to a day.
    bound = min(interval, _BOOT_PASS_CEILING_SECONDS)
    boot_deadline = asyncio.timeout(bound)
    try:
        async with boot_deadline:
            await reconciler.reconcile(trigger=BOOT_TRIGGER)
    except TimeoutError:
        # Only THIS deadline is tolerable. ``TimeoutError`` is also what a
        # socket timeout in the driver raises, and what any inner bound the
        # pass takes surfaces as, so an unqualified handler reports the
        # wrong cause and carries startup past a failure that is not the
        # one being waived. ``expired()`` is the scope's own answer to
        # which of the two happened.
        if not boot_deadline.expired():
            raise
        logger.warning(
            API_APP_STARTUP,
            service="sprint_recovery",
            note="boot pass hit its bound; the cadence covers what it missed",
            bound_seconds=bound,
        )


async def wire_sprint_recovery(app_state: AppState) -> None:
    """Run the boot recovery pass, then start its cadence.

    Idempotent for re-entered lifespans: returns early when a scheduler is
    already published.

    Raises:
        SubsystemDeclinedError: When a collaborator the sweep reads or
            writes through is absent, named so the status surface can
            report which.
        TimeoutError: When the boot pass raises one that is not this
            function's own startup deadline, which is the only timeout
            it waives.
    """
    engine_slice = app_state.slice(EngineStateSlice)
    if engine_slice.sprint_tail_scheduler is not None:
        return
    service = engine_slice.sprint_service
    if service is None:
        msg = "no sprint service; the sweep asks it whether sprints apply at all"
        raise SubsystemDeclinedError(msg)
    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None:
        msg = "no persistence backend; stranded sprints are read from it"
        raise SubsystemDeclinedError(msg)
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    if config_resolver is None:
        msg = "no settings resolver; the cadence and pause switch are read live"
        raise SubsystemDeclinedError(msg)
    sprint_repository = build_sprint_repository(persistence)
    if sprint_repository is None:
        msg = "no sprint repository for this backend; there is nothing to sweep"
        raise SubsystemDeclinedError(msg)

    reconciler = SprintRecoveryReconciler(
        sprints=sprint_repository,
        sprints_active=service.sprints_active,
    )
    interval = await config_resolver.get_float(
        "engine", "sprint_tail_resync_interval_seconds"
    )
    await _run_boot_pass(reconciler, config_resolver=config_resolver, interval=interval)
    scheduler = SprintTailScheduler(
        reconciler,
        interval_seconds=interval,
        config_resolver=config_resolver,
    )
    await scheduler.start()
    try:
        app_state.wire(EngineStateSlice, sprint_tail_scheduler=scheduler)
    except Exception:
        await scheduler.stop()
        raise
    logger.info(API_APP_STARTUP, service="sprint_recovery", note="wired")


async def unwire_sprint_recovery(app_state: AppState) -> None:
    """Stop the sweep and drop it from the slice."""
    scheduler = app_state.slice(EngineStateSlice).sprint_tail_scheduler
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
            service="sprint_recovery",
            note="sweep scheduler stop failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    engine_slice = app_state.slice(EngineStateSlice)
    app_state.swap_slice(
        engine_slice.model_copy(update={"sprint_tail_scheduler": None})
    )
    logger.info(API_APP_STARTUP, service="sprint_recovery", note="unwired")


__all__ = ["unwire_sprint_recovery", "wire_sprint_recovery"]
