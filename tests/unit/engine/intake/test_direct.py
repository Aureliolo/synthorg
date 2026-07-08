"""Unit tests for ``DirectIntake`` and the shared project resolver.

The direct strategy accepts every request and creates a task. These tests
pin that it files the task under the project the pipeline resolved (carried
on ``request.metadata["project"]``), falling back to its bound default only
when the request carries none.
"""

from typing import override

import pytest
from pydantic import JsonValue

from synthorg.client.models import ClientRequest, TaskRequirement
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.intake.strategies._shared import resolve_request_project
from synthorg.engine.intake.strategies.direct import DirectIntake
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


class _FakeTaskEngine(TaskEngine):
    """Minimal ``TaskEngine`` recording the ``create_task`` payload."""

    def __init__(self, *, next_id: str = "task-1") -> None:
        self.next_id = next_id
        self.captured_data: CreateTaskData | None = None

    @override
    async def create_task(self, data: CreateTaskData, *, requested_by: str) -> Task:
        del requested_by
        self.captured_data = data
        return Task(
            id=as_uuid(self.next_id),
            title=data.title,
            description=data.description,
            type=data.type,
            priority=data.priority,
            project=data.project,
            created_by=data.created_by,
        )


def _request(*, project: str | None = None) -> ClientRequest:
    metadata: dict[str, JsonValue] = {}
    if project is not None:
        metadata["project"] = project
    return ClientRequest(
        client_id="client-1",
        requirement=TaskRequirement(title="Build feature", description="Do the work."),
        metadata=metadata,
    )


class TestDirectIntakeProjectRouting:
    async def test_files_task_under_request_metadata_project(self) -> None:
        engine = _FakeTaskEngine()
        intake = DirectIntake(task_engine=engine)
        await intake.process(_request(project="tetris-project"))
        assert engine.captured_data is not None
        assert engine.captured_data.project == "tetris-project"

    async def test_falls_back_to_default_project_without_metadata(self) -> None:
        engine = _FakeTaskEngine()
        intake = DirectIntake(task_engine=engine, project=NotBlankStr("bound-default"))
        await intake.process(_request())
        assert engine.captured_data is not None
        assert engine.captured_data.project == "bound-default"


class TestResolveRequestProject:
    def test_prefers_metadata_project(self) -> None:
        got = resolve_request_project(
            _request(project="from-metadata"), NotBlankStr("default")
        )
        assert got == "from-metadata"

    def test_uses_default_when_absent(self) -> None:
        got = resolve_request_project(_request(), NotBlankStr("default"))
        assert got == "default"

    def test_uses_default_when_blank(self) -> None:
        got = resolve_request_project(_request(project="   "), NotBlankStr("default"))
        assert got == "default"

    def test_uses_default_when_non_string(self) -> None:
        request = ClientRequest(
            client_id="c1",
            requirement=TaskRequirement(title="t", description="d"),
            metadata={"project": 123},
        )
        assert resolve_request_project(request, NotBlankStr("default")) == "default"
