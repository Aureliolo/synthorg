"""Unit coverage for the real-work boot wiring.

``wire_real_intake_entry`` ensures the configured intake project
exists and attaches the :class:`IntakeEntryAdapter` to ``AppState``
once the work pipeline is online. ``wire_real_task_board_entry``
attaches the :class:`TaskBoardEntryAdapter` (no project bootstrap
since the board filing carries its own project). Both are no-ops
when the pipeline / simulation runtime is absent (empty company).
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.state import AppState
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.engine.pipeline.entry.boot import (
    _project_uuid,
    wire_real_intake_entry,
    wire_real_objective_entry,
    wire_real_task_board_entry,
)
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.state import EngineStateSlice
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(
    *,
    has_work_pipeline: bool,
    has_simulation_runtime: bool = True,
    project: Project | None = None,
    default_project: str | None = "client-intake",
    client_intake_enabled: bool = True,
) -> tuple[AppState, MagicMock]:
    projects = mock_of[ProjectRepository]()
    projects.get.return_value = project
    sim_state = mock_of[ClientSimulationState](
        intake_default_project=default_project,
    )
    # The intake door is gated on ``simulations.client_intake_enabled`` (off by
    # default); wire a resolver returning the flag plus a blank project value so
    # ``_resolve_intake_default_project`` falls back to the cached sim state.
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool.return_value = client_intake_enabled
    resolver.get_str.return_value = ""
    app_state = make_app_state(
        work_pipeline=mock_of[WorkPipeline]() if has_work_pipeline else None,
        client_simulation_state=sim_state if has_simulation_runtime else None,
        persistence=mock_of[PersistenceBackend](projects=projects),
        config_resolver=resolver,
    )
    return app_state, projects


async def test_noop_without_work_pipeline() -> None:
    app_state, projects = _app_state(has_work_pipeline=False)
    await wire_real_intake_entry(app_state)
    projects.get.assert_not_called()
    assert app_state.slice(EngineStateSlice).intake_entry_adapter is None


async def test_noop_without_simulation_runtime() -> None:
    app_state, projects = _app_state(
        has_work_pipeline=True,
        has_simulation_runtime=False,
    )
    await wire_real_intake_entry(app_state)
    projects.get.assert_not_called()
    assert app_state.slice(EngineStateSlice).intake_entry_adapter is None


async def test_noop_when_client_intake_disabled() -> None:
    """Off by default: no adapter is wired and no project is seeded."""
    app_state, projects = _app_state(
        has_work_pipeline=True,
        project=None,
        client_intake_enabled=False,
    )
    await wire_real_intake_entry(app_state)
    projects.create.assert_not_called()
    assert app_state.slice(EngineStateSlice).intake_entry_adapter is None


async def test_hot_swap_disabled_uninstalls_intake_adapter() -> None:
    """Toggling the door off unwires a previously-wired intake adapter."""
    app_state, _ = _app_state(
        has_work_pipeline=True,
        client_intake_enabled=False,
    )
    app_state.wire(EngineStateSlice, intake_entry_adapter=object())
    await wire_real_intake_entry(app_state, hot_swap=True)
    assert app_state.slice(EngineStateSlice).intake_entry_adapter is None


async def test_creates_project_when_absent_and_attaches_adapter() -> None:
    app_state, projects = _app_state(has_work_pipeline=True, project=None)
    await wire_real_intake_entry(app_state)
    projects.create.assert_called_once()
    created = projects.create.call_args.args[0]
    assert isinstance(created, Project)
    assert created.id == _project_uuid("client-intake")
    assert created.status is ProjectStatus.ACTIVE
    adapter = app_state.slice(EngineStateSlice).intake_entry_adapter
    assert isinstance(adapter, IntakeEntryAdapter)


async def test_skips_create_when_project_exists() -> None:
    existing = Project(id=_project_uuid("client-intake"), name="client-intake")
    app_state, projects = _app_state(has_work_pipeline=True, project=existing)
    await wire_real_intake_entry(app_state)
    projects.create.assert_not_called()
    assert app_state.slice(EngineStateSlice).intake_entry_adapter is not None


async def test_intake_default_project_read_live_from_db_resolver() -> None:
    """The intake project is read from the settings DB, not the cached state.

    Proves the hot path: a DB override of ``simulations.intake_default_project``
    is honoured at (re)wire time over the value baked into the cached
    ``ClientSimulationState``.
    """
    projects = mock_of[ProjectRepository]()
    projects.get.return_value = None
    sim_state = mock_of[ClientSimulationState](
        intake_default_project="cached-project",
    )
    resolver = cast(
        "ConfigResolver",
        mock_of[ConfigResolver](
            get_str=AsyncMock(return_value="db-project"),
            get_bool=AsyncMock(return_value=True),
        ),
    )
    app_state = make_app_state(
        work_pipeline=mock_of[WorkPipeline](),
        client_simulation_state=sim_state,
        persistence=mock_of[PersistenceBackend](projects=projects),
        config_resolver=resolver,
    )

    await wire_real_intake_entry(app_state)

    projects.create.assert_called_once()
    created = projects.create.call_args.args[0]
    assert created.id == _project_uuid("db-project")


async def test_hot_swap_uses_swap_seam() -> None:
    app_state, _ = _app_state(has_work_pipeline=True, project=None)
    # Pre-wire a sentinel so the once-only ``set`` seam would skip;
    # hot-swap must replace it via the ``swap`` seam.
    sentinel = object()
    app_state.wire(EngineStateSlice, intake_entry_adapter=sentinel)
    await wire_real_intake_entry(app_state, hot_swap=True)
    replaced = app_state.slice(EngineStateSlice).intake_entry_adapter
    assert replaced is not sentinel
    assert isinstance(replaced, IntakeEntryAdapter)


async def test_hot_swap_offline_uninstalls_intake_adapter() -> None:
    # A hot reload to an offline state (no work pipeline) must uninstall the
    # previously wired adapter; leaving it keeps routing through the stale
    # pipeline it captured at build time. A boot install (hot_swap=False) has
    # nothing wired, so only the hot-swap path clears.
    app_state, _ = _app_state(has_work_pipeline=False)
    app_state.wire(EngineStateSlice, intake_entry_adapter=object())
    await wire_real_intake_entry(app_state, hot_swap=True)
    assert app_state.slice(EngineStateSlice).intake_entry_adapter is None


async def test_hot_swap_offline_uninstalls_objective_adapter() -> None:
    app_state, _ = _app_state(has_work_pipeline=False)
    app_state.wire(EngineStateSlice, objective_entry_adapter=object())
    await wire_real_objective_entry(app_state, hot_swap=True)
    assert app_state.slice(EngineStateSlice).objective_entry_adapter is None


async def test_boot_offline_leaves_intake_adapter_untouched() -> None:
    # The boot path (hot_swap=False) must NOT clear: there is nothing wired yet,
    # and clearing would be a spurious write. A pre-existing sentinel survives.
    app_state, _ = _app_state(has_work_pipeline=False)
    sentinel = object()
    app_state.wire(EngineStateSlice, intake_entry_adapter=sentinel)
    await wire_real_intake_entry(app_state, hot_swap=False)
    assert app_state.slice(EngineStateSlice).intake_entry_adapter is sentinel


async def test_task_board_noop_without_work_pipeline() -> None:
    app_state, projects = _app_state(has_work_pipeline=False)
    await wire_real_task_board_entry(app_state)
    projects.get.assert_not_called()
    projects.create.assert_not_called()
    assert app_state.slice(EngineStateSlice).task_board_entry_adapter is None


async def test_task_board_noop_without_simulation_runtime() -> None:
    app_state, projects = _app_state(
        has_work_pipeline=True,
        has_simulation_runtime=False,
    )
    await wire_real_task_board_entry(app_state)
    projects.get.assert_not_called()
    projects.create.assert_not_called()
    assert app_state.slice(EngineStateSlice).task_board_entry_adapter is None


async def test_task_board_attaches_adapter_and_skips_project_bootstrap() -> None:
    app_state, projects = _app_state(has_work_pipeline=True, project=None)
    await wire_real_task_board_entry(app_state)
    # Board filings carry their own project; the helper does NOT
    # bootstrap a default project (the spine's project-existence
    # check surfaces missing projects per-filing).
    projects.get.assert_not_called()
    projects.create.assert_not_called()
    adapter = app_state.slice(EngineStateSlice).task_board_entry_adapter
    assert isinstance(adapter, TaskBoardEntryAdapter)


async def test_task_board_hot_swap_uses_swap_seam() -> None:
    app_state, _ = _app_state(has_work_pipeline=True, project=None)
    sentinel = object()
    app_state.wire(EngineStateSlice, task_board_entry_adapter=sentinel)
    await wire_real_task_board_entry(app_state, hot_swap=True)
    replaced = app_state.slice(EngineStateSlice).task_board_entry_adapter
    assert replaced is not sentinel
    assert isinstance(replaced, TaskBoardEntryAdapter)


async def test_task_board_hot_swap_offline_uninstalls_adapter() -> None:
    app_state, _ = _app_state(has_work_pipeline=False)
    app_state.wire(EngineStateSlice, task_board_entry_adapter=object())
    await wire_real_task_board_entry(app_state, hot_swap=True)
    assert app_state.slice(EngineStateSlice).task_board_entry_adapter is None


async def test_task_board_tolerates_unset_default_project() -> None:
    """The helper passes a placeholder ``default_project`` when the
    simulation runtime's ``intake_default_project`` is unset, since the
    factory contract requires a non-empty string even though the
    TASK_BOARD arm ignores it."""
    app_state, _ = _app_state(
        has_work_pipeline=True,
        default_project=None,
    )
    await wire_real_task_board_entry(app_state)
    adapter = app_state.slice(EngineStateSlice).task_board_entry_adapter
    assert isinstance(adapter, TaskBoardEntryAdapter)
