# module-kind: orchestrator
"""Startup wiring for the agile sprint service.

Constructs the :class:`SprintService` once the task engine, persistence,
ceremony scheduler, and settings resolver exist, and registers it as a
:class:`TaskEngine` observer so completions advance the sprint. Best-effort
+ idempotent: a boot missing any dependency leaves the service unwired
(its endpoints 503), and re-running after setup brings it online with no
restart. The observer is registered exactly once (guarded on first wire)
so a re-run cannot double-register it.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_sprint_service(app_state: AppState) -> None:
    """Build + wire the sprint service when its deps are present.

    Best-effort and idempotent. A missing task engine, persistence
    backend, ceremony scheduler, or settings resolver leaves the service
    unwired (its endpoints 503); re-running after those come online wires
    it live and registers the completion observer once.

    Raises:
        MemoryError: Propagated from construction; interpreter-level
            criticals are never swallowed by the best-effort handler.
        RecursionError: Propagated from construction for the same reason.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(EngineStateSlice).sprint_service is not None:
        # Already wired: never re-register the observer (register_observer
        # appends unconditionally, so a re-run would double-fire it).
        return

    persistence = app_state.slice(PersistenceStateSlice).backend
    task_engine = app_state.slice(EngineStateSlice).task_engine
    ceremony_scheduler = app_state.slice(EngineStateSlice).ceremony_scheduler
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    if (
        persistence is None
        or task_engine is None
        or ceremony_scheduler is None
        or config_resolver is None
    ):
        logger.info(
            API_APP_STARTUP,
            service="sprint_service",
            note="sprint service not wired; dependencies not yet present",
        )
        return
    try:
        from synthorg.engine.workflow.sprint_service import (  # noqa: PLC0415
            SprintService,
        )
        from synthorg.persistence.sprint_factory import (  # noqa: PLC0415
            build_sprint_repository,
        )

        sprint_repository = build_sprint_repository(persistence)
        if sprint_repository is None:
            logger.info(
                API_APP_STARTUP,
                service="sprint_service",
                note="sprint repository unavailable; service stays 503",
            )
            return
        service = SprintService(
            sprint_repository=sprint_repository,
            task_repository=persistence.tasks,
            ceremony_scheduler=ceremony_scheduler,
            config_resolver=config_resolver,
            sprint_config=app_state.config.workflow.sprint,
        )
        # Register the observer BEFORE committing the service to state: a
        # failure here then leaves the service unwired (endpoints stay 503,
        # as logged) and a re-run retries cleanly, rather than committing a
        # service whose completions never reach the observer.
        task_engine.register_observer(service.on_task_state_changed)
        app_state.wire(EngineStateSlice, sprint_service=service)
    except Exception as exc:  # noqa: BLE001 -- best-effort wiring: log and continue
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="sprint_service",
            note="sprint service wiring failed; endpoints stay 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="sprint_service", note="wired")
