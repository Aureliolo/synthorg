"""Boot wiring for the real client-request work-entry path.

Once the work pipeline spine is online, the real ``/requests``
approve path needs (1) the configured intake project to exist so the
pipeline's project-existence check passes, and (2) an
:class:`IntakeEntryAdapter` attached to ``AppState``. Both the boot
hook and the post-setup provider-reinit path call this; ``hot_swap``
selects the once-only vs replace seam. It is a logged no-op for an
empty company (no pipeline / no simulation runtime).
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import ProjectStatus
from synthorg.core.project import Project
from synthorg.engine.pipeline.entry.factory import build_work_entry_adapter
from synthorg.engine.pipeline.models import WorkSource
from synthorg.observability import get_logger
from synthorg.observability.events.client import CLIENT_SIMULATION_RUNTIME_WIRED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def wire_real_intake_entry(
    app_state: AppState,
    *,
    hot_swap: bool = False,
) -> None:
    """Ensure the intake project exists and attach the entry adapter.

    Args:
        app_state: Live application state (work pipeline, simulation
            runtime, persistence).
        hot_swap: When ``True`` replace an already-wired adapter
            (provider-reinit path); otherwise install once at boot.
    """
    if not app_state.has_work_pipeline or not app_state.has_simulation_runtime:
        logger.info(
            CLIENT_SIMULATION_RUNTIME_WIRED,
            service="intake_entry_adapter",
            mode="disabled",
            note="no work pipeline / simulation runtime; real intake offline",
        )
        return
    default_project = app_state.client_simulation_state.intake_default_project
    if not default_project:
        logger.warning(
            CLIENT_SIMULATION_RUNTIME_WIRED,
            service="intake_entry_adapter",
            mode="disabled",
            note="simulation runtime present but intake_default_project unset",
        )
        return
    await _ensure_project(app_state, default_project)
    adapter = build_work_entry_adapter(
        WorkSource.INTAKE,
        work_pipeline=app_state.work_pipeline,
        default_project=default_project,
    )
    if hot_swap:
        app_state.swap_intake_entry_adapter(adapter)
    else:
        app_state.set_intake_entry_adapter_if_absent(adapter)


async def _ensure_project(app_state: AppState, project_id: str) -> None:
    """Create the intake project if it does not already exist."""
    projects = app_state.persistence.projects
    if await projects.get(project_id) is not None:
        return
    await projects.create(
        Project(
            id=project_id,
            name=project_id,
            description="Default project for real client-request intake.",
            status=ProjectStatus.ACTIVE,
        )
    )
    logger.info(
        CLIENT_SIMULATION_RUNTIME_WIRED,
        service="intake_entry_adapter",
        note="created intake project",
        project=project_id,
    )
