# module-kind: orchestrator
"""Startup wiring for the initiative rollup service.

Constructs the :class:`ProjectRollupService` once the task engine and
persistence exist, and registers it as a :class:`TaskEngine` observer so a
task reaching a terminal status advances the plan and project behind it.
Best-effort and idempotent: a boot missing either dependency leaves the
service unwired, and re-running after setup brings it online with no restart.
A re-run after a successful wire is guarded by the state slice, so the observer
is not registered twice.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_project_rollup_service(app_state: AppState) -> None:
    """Build + wire the initiative rollup service when its deps are present.

    Raises:
        MemoryError: Propagated from construction; interpreter-level
            criticals are never swallowed by the best-effort handler.
        RecursionError: Propagated from construction for the same reason.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    if app_state.slice(EngineStateSlice).project_rollup_service is not None:
        # Already wired: never re-register the observer (register_observer
        # appends unconditionally, so a re-run would double-fire it).
        return

    persistence = app_state.slice(PersistenceStateSlice).backend
    task_engine = app_state.slice(EngineStateSlice).task_engine
    if persistence is None or task_engine is None:
        logger.info(
            API_APP_STARTUP,
            service="project_rollup_service",
            note="rollup not wired; dependencies not yet present",
        )
        return
    try:
        from synthorg.api.services.plan_service import PlanService  # noqa: PLC0415
        from synthorg.engine.initiative.rollup import (  # noqa: PLC0415
            ProjectRollupService,
        )

        service = ProjectRollupService(
            persistence=persistence,
            plan_status_writer=PlanService(
                repo=persistence.plans, clock=app_state.clock
            ),
            clock=app_state.clock,
        )
        # Register the observer BEFORE committing the service to state, so a
        # failure here leaves the service unwired and a re-run retries cleanly
        # rather than committing a service whose events never reach it. The
        # trade is deliberate: a failure between these two lines would let a
        # re-run register twice, which is why the state write follows
        # immediately and does no work of its own.
        task_engine.register_observer(service.on_task_state_changed)
        app_state.wire(EngineStateSlice, project_rollup_service=service)
    except Exception as exc:  # noqa: BLE001 -- best-effort wiring: log, continue
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="project_rollup_service",
            note="rollup wiring failed; initiative status will not advance",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="project_rollup_service", note="wired")
