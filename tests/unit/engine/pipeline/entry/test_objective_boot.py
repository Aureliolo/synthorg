"""Unit coverage for the real-objective boot wiring.

``wire_real_objective_entry`` ensures the configured objectives
project exists and attaches the :class:`ObjectiveEntryAdapter` to
``AppState`` once the work pipeline is online. It is a no-op when the
pipeline is absent (empty company).
"""

from collections.abc import Mapping
from typing import Any

import pytest

from synthorg.core.enums import ProjectStatus
from synthorg.core.project import Project
from synthorg.engine.pipeline.entry.boot import (
    _project_uuid,
    wire_real_objective_entry,
)
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
    project: Project | None = None,
) -> tuple[Any, Any]:
    projects = mock_of[ProjectRepository]()
    projects.get.return_value = project
    app_state = make_app_state(
        work_pipeline=mock_of[WorkPipeline]() if has_work_pipeline else None,
        persistence=mock_of[PersistenceBackend](projects=projects),
    )
    return app_state, projects


_EMPTY_ENV: Mapping[str, str] = {}


async def test_noop_without_work_pipeline() -> None:
    app_state, projects = _app_state(has_work_pipeline=False)
    await wire_real_objective_entry(app_state, env=_EMPTY_ENV)
    projects.get.assert_not_called()
    assert app_state.slice(EngineStateSlice).objective_entry_adapter is None


async def test_creates_project_when_absent_and_attaches_adapter() -> None:
    app_state, projects = _app_state(has_work_pipeline=True, project=None)
    await wire_real_objective_entry(app_state, env=_EMPTY_ENV)
    created = projects.create.call_args.args[0]
    assert isinstance(created, Project)
    assert created.id == _project_uuid("objectives")
    assert created.status is ProjectStatus.ACTIVE
    adapter = app_state.slice(EngineStateSlice).objective_entry_adapter
    assert isinstance(adapter, ObjectiveEntryAdapter)


async def test_skips_create_when_project_exists() -> None:
    existing = Project(id=_project_uuid("objectives"), name="objectives")
    app_state, projects = _app_state(has_work_pipeline=True, project=existing)
    await wire_real_objective_entry(app_state, env=_EMPTY_ENV)
    projects.create.assert_not_called()
    assert app_state.slice(EngineStateSlice).objective_entry_adapter is not None


async def test_hot_swap_uses_swap_seam() -> None:
    app_state, projects = _app_state(has_work_pipeline=True, project=None)
    sentinel = object()
    app_state.wire(EngineStateSlice, objective_entry_adapter=sentinel)
    await wire_real_objective_entry(app_state, hot_swap=True, env=_EMPTY_ENV)
    projects.create.assert_called_once()
    replaced = app_state.slice(EngineStateSlice).objective_entry_adapter
    assert replaced is not sentinel
    assert isinstance(replaced, ObjectiveEntryAdapter)
