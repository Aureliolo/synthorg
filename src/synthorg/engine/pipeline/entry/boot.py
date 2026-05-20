"""Boot wiring for the real work-entry paths.

Once the work pipeline spine is online, the real ``/requests``
approve path and the real ``/objectives`` POST path each need their
configured default project to exist (so the pipeline's
project-existence check passes) and their entry adapter attached to
``AppState``. The two boot hooks below take this on. ``hot_swap``
selects the once-only vs replace seam in both. They are logged
no-ops for an empty company (no pipeline / no simulation runtime).
"""

import os
from typing import TYPE_CHECKING

from synthorg.core.enums import ProjectStatus
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.engine.pipeline.entry.factory import build_work_entry_adapter
from synthorg.engine.pipeline.models import WorkSource
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.client import CLIENT_SIMULATION_RUNTIME_WIRED
from synthorg.observability.events.objectives import OBJECTIVE_ENTRY_WIRED
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.api.state import AppState
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_OBJECTIVES_DEFAULT_PROJECT_KEY = "default_project"


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
    await _ensure_project(
        app_state,
        default_project,
        service="intake_entry_adapter",
        event=CLIENT_SIMULATION_RUNTIME_WIRED,
        description="Default project for real client-request intake.",
    )
    adapter = build_work_entry_adapter(
        WorkSource.INTAKE,
        work_pipeline=app_state.work_pipeline,
        default_project=default_project,
    )
    if hot_swap:
        app_state.swap_intake_entry_adapter(adapter)
    else:
        app_state.set_intake_entry_adapter_if_absent(adapter)


async def wire_real_objective_entry(
    app_state: AppState,
    *,
    hot_swap: bool = False,
    env: Mapping[str, str] = os.environ,
) -> None:
    """Ensure the objectives project exists and attach the entry adapter.

    Unlike the intake hook this does NOT depend on the client
    simulation runtime: a configured provider (i.e.
    ``has_work_pipeline``) is sufficient. The objectives default
    project is resolved via the bootstrap settings resolver (env >
    registered default).

    Args:
        app_state: Live application state (work pipeline,
            persistence).
        hot_swap: When ``True`` replace an already-wired adapter
            (provider-reinit path); otherwise install once at boot.
        env: Environment mapping override for tests.
    """
    if not app_state.has_work_pipeline:
        logger.info(
            OBJECTIVE_ENTRY_WIRED,
            service="objective_entry_adapter",
            mode="disabled",
            note="no work pipeline; real objective entry offline",
        )
        return
    default_project = str(
        resolve_init_value(
            SettingNamespace.OBJECTIVES,
            _OBJECTIVES_DEFAULT_PROJECT_KEY,
            env=env,
        ).value
    ).strip()
    if not default_project:
        logger.warning(
            OBJECTIVE_ENTRY_WIRED,
            service="objective_entry_adapter",
            mode="disabled",
            note="objectives.default_project resolved blank",
        )
        return
    await _ensure_project(
        app_state,
        default_project,
        service="objective_entry_adapter",
        event=OBJECTIVE_ENTRY_WIRED,
        description="Default project for real goal/objective intake.",
    )
    adapter = build_work_entry_adapter(
        WorkSource.OBJECTIVE,
        work_pipeline=app_state.work_pipeline,
        default_project=default_project,
    )
    if hot_swap:
        app_state.swap_objective_entry_adapter(adapter)
    else:
        app_state.set_objective_entry_adapter_if_absent(adapter)


async def _ensure_project(
    app_state: AppState,
    project_id: NotBlankStr,
    *,
    service: str,
    event: str,
    description: str,
) -> None:
    """Create ``project_id`` if it does not already exist.

    The ``get`` fast-path skips a redundant create on the common
    already-exists case, but ``create`` is still guarded: a
    concurrent winner (boot racing a provider-reinit ``wire_real_*_entry``)
    surfaces as ``DuplicateRecordError``, which is benign here (the
    project exists, which is the post-condition we want). Any other
    failure is logged at ERROR with the project id before propagating
    so a boot abort is actionable rather than an opaque traceback.
    """
    projects = app_state.persistence.projects
    if await projects.get(project_id) is not None:
        return
    try:
        await projects.create(
            Project(
                id=project_id,
                name=project_id,
                description=description,
                status=ProjectStatus.ACTIVE,
            )
        )
    except DuplicateRecordError:
        return
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.error(
            event,
            service=service,
            note="failed to create default project",
            project=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(
        event,
        service=service,
        note="created default project",
        project=project_id,
    )
