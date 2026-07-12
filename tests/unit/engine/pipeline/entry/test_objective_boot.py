"""Unit coverage for the real-objective boot wiring.

``wire_real_objective_entry`` attaches the
:class:`ObjectiveEntryAdapter` to ``AppState`` once the work pipeline
and persistence are online. It seeds no project (the adapter mints a
per-initiative project per submission); it is a no-op when the
pipeline or persistence is absent (empty company).
"""

from unittest.mock import MagicMock

import pytest

from synthorg.api.state import AppState
from synthorg.engine.pipeline.entry.boot import wire_real_objective_entry
from synthorg.engine.pipeline.entry.objective_adapter import ObjectiveEntryAdapter
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.state import EngineStateSlice
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(
    *,
    has_work_pipeline: bool,
    has_persistence: bool = True,
) -> tuple[AppState, MagicMock]:
    projects = mock_of[ProjectRepository]()
    projects.get.return_value = None
    persistence = (
        mock_of[PersistenceBackend](projects=projects) if has_persistence else None
    )
    app_state = make_app_state(
        work_pipeline=mock_of[WorkPipeline]() if has_work_pipeline else None,
        persistence=persistence,
    )
    return app_state, projects


async def test_noop_without_work_pipeline() -> None:
    app_state, projects = _app_state(has_work_pipeline=False)
    await wire_real_objective_entry(app_state)
    projects.create.assert_not_called()
    assert app_state.slice(EngineStateSlice).objective_entry_adapter is None


async def test_noop_without_persistence() -> None:
    app_state, projects = _app_state(has_work_pipeline=True, has_persistence=False)
    await wire_real_objective_entry(app_state)
    projects.create.assert_not_called()
    assert app_state.slice(EngineStateSlice).objective_entry_adapter is None


async def test_attaches_adapter_without_seeding_a_project() -> None:
    app_state, projects = _app_state(has_work_pipeline=True)
    await wire_real_objective_entry(app_state)
    # No project is seeded at wire time; the adapter mints one per submission.
    projects.create.assert_not_called()
    adapter = app_state.slice(EngineStateSlice).objective_entry_adapter
    assert isinstance(adapter, ObjectiveEntryAdapter)
    # The adapter mints projects through the live persistence repository.
    assert adapter._project_repo is projects


async def test_hot_swap_uses_swap_seam() -> None:
    app_state, _projects = _app_state(has_work_pipeline=True)
    sentinel = object()
    app_state.wire(EngineStateSlice, objective_entry_adapter=sentinel)
    await wire_real_objective_entry(app_state, hot_swap=True)
    replaced = app_state.slice(EngineStateSlice).objective_entry_adapter
    assert replaced is not sentinel
    assert isinstance(replaced, ObjectiveEntryAdapter)


async def test_hot_swap_without_pipeline_clears_adapter() -> None:
    """A hot reload that lost the pipeline uninstalls the stale adapter."""
    app_state, _projects = _app_state(has_work_pipeline=False)
    sentinel = object()
    app_state.wire(EngineStateSlice, objective_entry_adapter=sentinel)
    await wire_real_objective_entry(app_state, hot_swap=True)
    assert app_state.slice(EngineStateSlice).objective_entry_adapter is None
