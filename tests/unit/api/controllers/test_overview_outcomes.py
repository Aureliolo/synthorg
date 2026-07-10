"""Unit tests for the overview task-outcome breakdown resolver.

Verifies that ``_resolve_task_outcomes`` classifies terminal tasks into
succeeded / empty / failed from real artifact counts, so the dashboard tile
surfaces failed and empty runs distinctly instead of a generic in-review count.
"""

import pytest

from synthorg.api.controllers.analytics.overview import _resolve_task_outcomes
from synthorg.core.artifact import Artifact, ArtifactType
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from tests._shared import as_uuid, make_app_state
from tests.unit.api.fakes import FakeArtifactRepository
from tests.unit.api.fakes_backend import FakePersistenceBackend


def _task(label: str, status: TaskStatus) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description="Overview outcome fixture.",
        type=TaskType.DEVELOPMENT,
        project="project-1",
        priority=Priority.MEDIUM,
        status=status,
        assigned_to="agent-1",
        created_by="engine",
    )


def _artifact_for(task: Task) -> Artifact:
    return Artifact(
        id=f"artifact-{task.id}",
        type=ArtifactType.CODE,
        path="src/main.py",
        task_id=str(task.id),
        created_by="agent-1",
    )


@pytest.mark.unit
async def test_resolve_task_outcomes_classifies_terminal_runs() -> None:
    backend = FakePersistenceBackend()
    backend.mark_connected()
    failed = _task("failed", TaskStatus.FAILED)
    empty = _task("empty", TaskStatus.IN_REVIEW)  # no artifacts -> empty
    ok = _task("ok", TaskStatus.COMPLETED)
    await backend.artifacts.save(_artifact_for(ok))  # produced output -> succeeded
    running = _task("running", TaskStatus.IN_PROGRESS)  # non-terminal -> ignored

    app_state = make_app_state(persistence=backend)
    outcomes = await _resolve_task_outcomes(app_state, (failed, empty, ok, running))

    assert outcomes == {
        RunOutcome.SUCCEEDED.value: 1,
        RunOutcome.EMPTY.value: 1,
        RunOutcome.FAILED.value: 1,
    }


@pytest.mark.unit
async def test_resolve_task_outcomes_credits_finished_run_without_backend() -> None:
    # No persistence backend: a finished run is credited as succeeded rather
    # than fabricating an empty outcome.
    app_state = make_app_state(persistence=None)
    ok = _task("ok", TaskStatus.COMPLETED)
    outcomes = await _resolve_task_outcomes(app_state, (ok,))
    assert outcomes[RunOutcome.SUCCEEDED.value] == 1
    assert outcomes[RunOutcome.EMPTY.value] == 0


@pytest.mark.unit
async def test_resolve_task_outcomes_empty_when_no_terminal_tasks() -> None:
    app_state = make_app_state(persistence=None)
    outcomes = await _resolve_task_outcomes(
        app_state, (_task("running", TaskStatus.IN_PROGRESS),)
    )
    assert outcomes == {
        RunOutcome.SUCCEEDED.value: 0,
        RunOutcome.EMPTY.value: 0,
        RunOutcome.FAILED.value: 0,
    }


def test_fake_artifact_repository_available() -> None:
    # Guard: the resolver relies on the backend exposing an artifact repo.
    assert isinstance(FakePersistenceBackend().artifacts, FakeArtifactRepository)
