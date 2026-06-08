"""Unit coverage for :class:`IntakeEntryAdapter`.

The adapter is the thin real work-entry seam: it maps a stored
``ClientRequest`` onto a ``WorkItem`` and hands it to the work
pipeline spine. It owns no request-store / lock / reconciliation
(that stays in the controller background task).
"""

from typing import Any

import pytest

from synthorg.client.models import ClientRequest, TaskRequirement
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
from synthorg.engine.pipeline.errors import WorkIntakeRejectedError
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.pipeline.protocol import WorkPipeline
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_DEFAULT_PROJECT = "client-intake"


def _request(**overrides: Any) -> ClientRequest:
    base: dict[str, Any] = {
        "request_id": "req-42",
        "client_id": "acme-co",
        "requirement": TaskRequirement(
            title="Ship the status endpoint",
            description="Return a JSON status body from /status.",
            task_type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            estimated_complexity=Complexity.COMPLEX,
            acceptance_criteria=("returns 200", "body has status key"),
        ),
    }
    base.update(overrides)
    return ClientRequest(**base)


def _result(work_item: WorkItem) -> WorkPipelineResult:
    return WorkPipelineResult(
        work_item=work_item,
        verdict=RoutingVerdict.LEAF,
        execution_path=ExecutionPath.SOLO,
        task_id="task-7",
        final_task_status=TaskStatus.IN_REVIEW,
        phases=(WorkPhaseResult(phase="intake", success=True, duration_seconds=0.0),),
        total_duration_seconds=0.1,
    )


def _adapter(pipeline: WorkPipeline) -> IntakeEntryAdapter:
    return IntakeEntryAdapter(
        work_pipeline=pipeline,
        default_project=_DEFAULT_PROJECT,
    )


def test_is_work_entry_adapter() -> None:
    adapter = _adapter(mock_of[WorkPipeline]())
    assert isinstance(adapter, WorkEntryAdapter)
    assert adapter.source is WorkSource.INTAKE


async def test_submit_maps_request_to_work_item() -> None:
    pipeline = mock_of[WorkPipeline]()
    captured: dict[str, WorkItem] = {}

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        captured["item"] = work_item
        return _result(work_item)

    pipeline.run.side_effect = _run
    request = _request()

    result = await _adapter(pipeline).submit(request)

    item = captured["item"]
    assert item.source is WorkSource.INTAKE
    assert item.correlation_id == request.request_id
    assert item.project == _DEFAULT_PROJECT
    assert item.title == request.requirement.title
    assert item.raw_intent == request.requirement.description
    assert item.requested_by == request.client_id
    assert item.priority is Priority.HIGH
    assert item.task_type is TaskType.DEVELOPMENT
    assert item.estimated_complexity is Complexity.COMPLEX
    assert item.acceptance_criteria == request.requirement.acceptance_criteria
    assert result.task_id == "task-7"


async def test_submit_folds_scoping_notes_into_raw_intent() -> None:
    pipeline = mock_of[WorkPipeline]()
    captured: dict[str, WorkItem] = {}

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        captured["item"] = work_item
        return _result(work_item)

    pipeline.run.side_effect = _run
    request = _request(metadata={"scoping_notes": "Focus on the JSON shape."})

    await _adapter(pipeline).submit(request)

    raw = captured["item"].raw_intent
    assert request.requirement.description in raw
    assert "## Reviewer scoping notes" in raw
    assert "Focus on the JSON shape." in raw


async def test_submit_propagates_pipeline_error() -> None:
    pipeline = mock_of[WorkPipeline]()
    pipeline.run.side_effect = WorkIntakeRejectedError("nope")
    with pytest.raises(WorkIntakeRejectedError):
        await _adapter(pipeline).submit(_request())
