# module-kind: orchestrator
"""Startup wiring for the long-horizon project brain.

Builds the brain service and its per-task tool factory once persistence, a
project workspace, and a memory backend are all present, then replays any
brain entries whose index write failed. Best-effort throughout: a missing
collaborator leaves the brain controllers and MCP handlers to 503 rather than
poisoning startup.
"""

from typing import Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.project_brain.factory import ProjectBrainRuntime

logger = get_logger(__name__)

#: Upper bound on the projects scanned for an index replay at boot. Large
#: enough to cover any realistic org, small enough to stay a bounded read.
_REPLAY_PROJECT_SCAN_LIMIT: Final[int] = 10_000


async def wire_project_brain(app_state: AppState) -> None:
    """Wire the long-horizon project brain once persistence + workspace exist.

    Best-effort and gated on a connected persistence backend, a project
    workspace, and a memory backend (the brain indexes entries for RAG re-entry
    and commits snapshots through the workspace). The shared
    :class:`ProjectAwareMemoryFacade` already fans out to the brain leg (the docs
    factory builds it with ``brain_enabled=True``); this hook builds the service
    and the per-task tool factory and parks them on the state slice. A missing
    collaborator leaves the brain controllers + MCP handlers to 503 rather than
    poisoning startup.
    """
    from synthorg.engine.workspace.state import WorkspaceStateSlice  # noqa: PLC0415
    from synthorg.memory.state import (  # noqa: PLC0415
        MemoryStateSlice,
        memory_backend_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.project_brain.state import ProjectBrainStateSlice  # noqa: PLC0415

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    workspace_service = app_state.slice(WorkspaceStateSlice).project_workspace_service
    if workspace_service is None:
        return
    if app_state.slice(ProjectBrainStateSlice).service is not None:
        return
    if app_state.slice(MemoryStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="project_brain",
            note="memory backend not wired; project brain wiring skipped",
        )
        return
    from synthorg.project_brain.factory import (  # noqa: PLC0415
        build_project_brain_service,
    )

    runtime = build_project_brain_service(
        repo=persistence_of(app_state).project_brain,
        workspace_service=workspace_service,
        git_backend=workspace_service.git_backend,
        memory_backend=memory_backend_of(app_state),
        clock=app_state.clock,
    )
    app_state.swap_slice(
        ProjectBrainStateSlice(
            service=runtime.brain_service,
            tool_factory=runtime.tool_factory,
        )
    )
    logger.info(API_APP_STARTUP, service="project_brain", note="wired")
    await _replay_project_brain_index(app_state, runtime)


async def _replay_project_brain_index(
    app_state: AppState,
    runtime: ProjectBrainRuntime,
) -> None:
    """Best-effort boot replay of the brain RAG index gap.

    Re-indexes brain entries that were persisted but whose index write failed
    (so they are invisible to transparent re-entry retrieval). Never poisons
    startup: a failure is logged and swallowed.
    """
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    try:
        projects = await persistence_of(app_state).projects.list_items(
            limit=_REPLAY_PROJECT_SCAN_LIMIT
        )
        project_ids = tuple(str(project.id) for project in projects)
        if not project_ids:
            return
        reindexed = await runtime.replay_unindexed(project_ids=project_ids)
        logger.info(
            API_APP_STARTUP,
            service="project_brain",
            note="index replay complete",
            reindexed=reindexed,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort boot replay; never poison start
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="project_brain",
            note="index replay skipped",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
