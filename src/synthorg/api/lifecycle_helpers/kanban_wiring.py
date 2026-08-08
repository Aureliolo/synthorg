# module-kind: orchestrator
"""Startup wiring for the Kanban board service.

Constructs the :class:`KanbanBoardService` once the task engine, persistence,
and settings resolver exist, so the board API can project tasks onto columns
and drive column moves. Best-effort + idempotent: a boot that lacks any
dependency (empty-company start before persistence) leaves the board
unwired, and re-running after setup brings it online with no restart.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_kanban_board(app_state: AppState) -> None:
    """Build + wire the Kanban board service when its deps are present.

    Best-effort and idempotent. A missing task engine, persistence backend,
    or settings resolver leaves the board service unwired (its endpoints
    503); re-running after those come online wires it live.

    Raises:
        MemoryError: Propagated from construction; interpreter-level
            criticals are never swallowed by the best-effort handler.
        RecursionError: Propagated from construction for the same reason.
        SubsystemDeclinedError: A collaborator the board reads through is
            absent, named so the status surface can report which.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    persistence = app_state.slice(PersistenceStateSlice).backend
    task_engine = app_state.slice(EngineStateSlice).task_engine
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    if persistence is None:
        msg = "no persistence backend; the board projects durable task rows"
        raise SubsystemDeclinedError(msg)
    if task_engine is None:
        msg = "no task engine; a board move is a task transition"
        raise SubsystemDeclinedError(msg)
    if config_resolver is None:
        msg = "no settings resolver; the WIP limits are settings-driven"
        raise SubsystemDeclinedError(msg)
    try:
        from synthorg.engine.workflow.kanban_service import (  # noqa: PLC0415
            KanbanBoardService,
        )

        service = KanbanBoardService(
            task_repository=persistence.tasks,
            task_engine=task_engine,
            config_resolver=config_resolver,
            # Advisory sprint gate: present once the sprint service is wired
            # (sprint wiring runs first), None on an early boot. Re-wiring the
            # board after the sprint service comes online picks it up.
            sprint_service=app_state.slice(EngineStateSlice).sprint_service,
        )
        app_state.wire(EngineStateSlice, kanban_board_service=service)
    except Exception as exc:  # noqa: BLE001 -- best-effort wiring: log and continue
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="kanban_board",
            note="kanban board wiring failed; board endpoints stay 503",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="kanban_board", note="wired")
