"""Boot wiring for the real work-entry paths.

Once the work pipeline spine is online:

* :func:`wire_real_intake_entry` ensures the configured intake project
  exists and attaches an :class:`IntakeEntryAdapter` to ``AppState``
  (the ``POST /requests/{id}/approve`` path).
* :func:`wire_real_objective_entry` ensures the configured objectives
  default project exists and attaches an
  :class:`ObjectiveEntryAdapter` to ``AppState`` (the ``POST
  /objectives`` path).
* :func:`wire_real_task_board_entry` attaches a
  :class:`TaskBoardEntryAdapter` to ``AppState`` (the ``POST /tasks``
  path). The board input carries its own project, so no project
  bootstrap is performed.

All three helpers are called by the boot hook and by the post-setup
provider-reinit path; ``hot_swap`` selects the once-only vs replace
seam. Each is a logged no-op for an empty company (no pipeline / no
simulation runtime).
"""

import os
from typing import TYPE_CHECKING

from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import ProjectStatus
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.engine.pipeline.entry.factory import (
    build_brownfield_entry_adapter,
    build_work_entry_adapter,
)
from synthorg.engine.pipeline.forecast_gate import ForecastGate
from synthorg.engine.pipeline.models import WorkSource
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.brownfield import BROWNFIELD_ENTRY_WIRED
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


def _forecast_gate_for(app_state: AppState) -> ForecastGate | None:
    """Build a :class:`ForecastGate` from AppState when its deps are wired.

    Returns ``None`` when the cost-dial services are absent (empty
    company / boot order race) so the adapter falls back to a direct
    pipeline dispatch. Once the cost-dial wiring lands the gate is
    constructed against the live work pipeline + persisted repo +
    forecaster + budget config.
    """
    forecaster = app_state.cost_forecaster
    repo = app_state.cost_forecast_repo
    budget_config = app_state.budget_config
    if forecaster is None or repo is None or budget_config is None:
        # With persistence up the cost-dial set wires atomically before
        # this seam, so a missing forecaster/repo while forecasts are
        # REQUIRED is a genuine partial-wire failure -- fail fast rather
        # than silently dispatch work past a gate the operator mandated.
        # The no-persistence / empty-company path stays tolerated (no
        # work pipeline reaches here, and the gate is legitimately None).
        if (
            app_state.has_persistence
            and budget_config is not None
            and budget_config.forecast_required
        ):
            msg = (
                "budget.forecast_required is enabled but the cost-dial"
                " forecaster/repository did not wire; refusing to dispatch"
                " work past a required pre-flight forecast gate"
            )
            raise ServiceUnavailableError(msg)
        return None
    return ForecastGate(
        work_pipeline=app_state.work_pipeline,
        forecaster=forecaster,
        forecast_repo=repo,
        budget_config=budget_config,
    )


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
        forecast_gate=_forecast_gate_for(app_state),
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
        forecast_gate=_forecast_gate_for(app_state),
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
        log_exception_redacted(
            logger,
            event,
            exc,
            service=service,
            note="failed to create default project",
            project=project_id,
        )
        raise
    logger.info(
        event,
        service=service,
        note="created default project",
        project=project_id,
    )


async def wire_real_task_board_entry(
    app_state: AppState,
    *,
    hot_swap: bool = False,
) -> None:
    """Attach the task-board work-entry adapter to ``AppState``.

    Distinct from :func:`wire_real_intake_entry`: the board's project
    is supplied per-filing by the user, so this helper does NOT
    bootstrap a default project. The pipeline's project-existence
    check runs against whatever ``project`` the filing carries; an
    unknown project surfaces as ``WorkProjectNotFoundError`` from the
    pipeline (the same shape the intake path uses for its own checks),
    which the background coroutine in the controller logs.

    Args:
        app_state: Live application state (work pipeline, simulation
            runtime).
        hot_swap: When ``True`` replace an already-wired adapter
            (provider-reinit path); otherwise install once at boot.
    """
    if not app_state.has_work_pipeline or not app_state.has_simulation_runtime:
        logger.info(
            CLIENT_SIMULATION_RUNTIME_WIRED,
            service="task_board_entry_adapter",
            mode="disabled",
            note="no work pipeline / simulation runtime; task board offline",
        )
        return
    # ``default_project`` is the factory's INTAKE-arm kwarg; the
    # TASK_BOARD arm ignores it. Pass the same value the intake
    # helper uses when present, or a placeholder constant otherwise:
    # the factory contract requires a non-empty string here even
    # though the TASK_BOARD branch discards it.
    default_project = (
        app_state.client_simulation_state.intake_default_project or "task-board"
    )
    adapter = build_work_entry_adapter(
        WorkSource.TASK_BOARD,
        work_pipeline=app_state.work_pipeline,
        default_project=default_project,
        forecast_gate=_forecast_gate_for(app_state),
    )
    if hot_swap:
        app_state.swap_task_board_entry_adapter(adapter)
    else:
        app_state.set_task_board_entry_adapter_if_absent(adapter)


async def wire_real_brownfield_entry(
    app_state: AppState,
    *,
    hot_swap: bool = False,
) -> None:
    """Attach the brownfield codebase-intake entry adapter to ``AppState``.

    Gated on the work pipeline plus the import collaborators: a connected
    persistence backend (for the structure-map repo + project workspace),
    a wired :class:`ProjectWorkspaceService`, and a wired
    :class:`KnowledgeService` (the codebase index target). A missing
    collaborator is a logged no-op so a partial boot does not poison
    startup; the ``/brownfield`` controller then honestly 503s.

    Args:
        app_state: Live application state.
        hot_swap: When ``True`` replace an already-wired adapter
            (provider-reinit path); otherwise install once at boot.
    """
    workspace_service = (
        app_state.project_workspace_service if app_state.has_persistence else None
    )
    knowledge_service = app_state.knowledge_service
    if (
        not app_state.has_work_pipeline
        or workspace_service is None
        or knowledge_service is None
    ):
        logger.info(
            BROWNFIELD_ENTRY_WIRED,
            service="brownfield_entry_adapter",
            mode="disabled",
            note="missing work pipeline / workspace service / knowledge service",
        )
        return
    from synthorg.engine.brownfield.scanner import (  # noqa: PLC0415
        build_structure_map_scanners,
    )
    from synthorg.engine.brownfield.service import (  # noqa: PLC0415
        BrownfieldImportService,
    )
    from synthorg.engine.brownfield.source_resolver import (  # noqa: PLC0415
        BrownfieldSourceResolver,
    )
    from synthorg.tools.structure_map.tool_factory import (  # noqa: PLC0415
        build_structure_map_tool_factory,
    )

    structure_map_repo = app_state.persistence.codebase_structure_maps
    app_state.set_structure_map_tool_factory(
        build_structure_map_tool_factory(repository=structure_map_repo)
    )
    catalog = app_state.connection_catalog if app_state.has_connection_catalog else None
    import_service = BrownfieldImportService(
        workspace_service=workspace_service,
        source_resolver=BrownfieldSourceResolver(connection_catalog=catalog),
        scanners=build_structure_map_scanners(),
        structure_map_repo=structure_map_repo,
        knowledge_service=knowledge_service,
        clock=app_state.clock,
    )
    adapter = build_brownfield_entry_adapter(
        work_pipeline=app_state.work_pipeline,
        import_service=import_service,
        forecast_gate=_forecast_gate_for(app_state),
    )
    if hot_swap:
        app_state.swap_brownfield_entry_adapter(adapter)
    else:
        app_state.set_brownfield_entry_adapter_if_absent(adapter)
