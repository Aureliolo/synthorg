"""Unit coverage for the real-work boot wiring.

``wire_real_intake_entry`` ensures the configured intake project
exists and attaches the :class:`IntakeEntryAdapter` to ``AppState``
once the work pipeline is online. ``wire_real_task_board_entry``
attaches the :class:`TaskBoardEntryAdapter` (no project bootstrap
since the board filing carries its own project). Both are no-ops
when the pipeline / simulation runtime is absent (empty company).
"""

from unittest.mock import MagicMock

import pytest

from synthorg.api.state import AppState
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.engine.pipeline.entry.boot import (
    _project_uuid,
    wire_real_intake_entry,
    wire_real_task_board_entry,
)
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.state import EngineStateSlice
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(
    *,
    has_work_pipeline: bool,
    has_simulation_runtime: bool = True,
    project: Project | None = None,
    default_project: str | None = "client-intake",
) -> tuple[AppState, MagicMock]:
    projects = mock_of[ProjectRepository]()
    projects.get.return_value = project
    sim_state = mock_of[ClientSimulationState](
        intake_default_project=default_project,
    )
    app_state = make_app_state(
        work_pipeline=mock_of[WorkPipeline]() if has_work_pipeline else None,
        client_simulation_state=sim_state if has_simulation_runtime else None,
        persistence=mock_of[PersistenceBackend](projects=projects),
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


async def test_creates_project_when_absent_and_attaches_adapter() -> None:
    app_state, projects = _app_state(has_work_pipeline=True, project=None)
    await wire_real_intake_entry(app_state)
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
