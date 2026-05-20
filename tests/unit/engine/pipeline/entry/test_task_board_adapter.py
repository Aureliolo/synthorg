"""Unit coverage for :class:`TaskBoardEntryAdapter`.

The adapter is the thin board work-entry seam: it maps a board
``TaskBoardFiling`` onto a ``WorkItem`` with ``source=TASK_BOARD`` and
hands it to the work pipeline spine. It owns no task store, no lock,
and performs no task-lifecycle reconciliation; the controller
background task drives reconciliation (the pipeline creates the task
inside its intake phase).
"""

from typing import Any

import pytest

from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.pipeline.entry.task_board_adapter import (
    TaskBoardEntryAdapter,
    TaskBoardFiling,
)
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


def _filing(**overrides: Any) -> TaskBoardFiling:
    base: dict[str, Any] = {
        "title": "Ship the status endpoint",
        "description": "Return a JSON status body from /status.",
        "task_type": TaskType.DEVELOPMENT,
        "priority": Priority.HIGH,
        "project": "platform",
        "requested_by": "user-42",
        "estimated_complexity": Complexity.COMPLEX,
    }
    base.update(overrides)
    return TaskBoardFiling(**base)


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


def _adapter(pipeline: WorkPipeline) -> TaskBoardEntryAdapter:
    return TaskBoardEntryAdapter(work_pipeline=pipeline)


def test_source_is_task_board() -> None:
    adapter = _adapter(mock_of[WorkPipeline]())
    assert adapter.source is WorkSource.TASK_BOARD


def test_filing_auto_correlation_id_is_set() -> None:
    filing = _filing()
    assert filing.correlation_id  # default-factory populated


def test_filing_explicit_correlation_id_preserved() -> None:
    filing = _filing(correlation_id="explicit-corr-1")
    assert filing.correlation_id == "explicit-corr-1"


def test_filing_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match=r"[Ee]xtra"):
        TaskBoardFiling(
            title="x",
            description="y",
            task_type=TaskType.DEVELOPMENT,
            project="p",
            requested_by="u",
            unknown_field="boom",  # type: ignore[call-arg]
        )


async def test_submit_maps_filing_to_work_item() -> None:
    pipeline = mock_of[WorkPipeline]()
    captured: dict[str, WorkItem] = {}

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        captured["item"] = work_item
        return _result(work_item)

    pipeline.run.side_effect = _run
    filing = _filing()

    result = await _adapter(pipeline).submit(filing)

    item = captured["item"]
    assert item.source is WorkSource.TASK_BOARD
    assert item.origin_adapter_id == "task-board-entry-adapter"
    assert item.correlation_id == filing.correlation_id
    assert item.title == filing.title
    assert item.raw_intent == filing.description
    assert item.project == filing.project
    assert item.requested_by == filing.requested_by
    assert item.priority is Priority.HIGH
    assert item.task_type is TaskType.DEVELOPMENT
    assert item.estimated_complexity is Complexity.COMPLEX
    assert item.acceptance_criteria == ()
    assert result.task_id == "task-7"


async def test_submit_propagates_pipeline_error() -> None:
    pipeline = mock_of[WorkPipeline]()
    pipeline.run.side_effect = WorkIntakeRejectedError("nope")
    with pytest.raises(WorkIntakeRejectedError):
        await _adapter(pipeline).submit(_filing())


async def test_submit_propagates_memory_error() -> None:
    """``MemoryError`` must surface to the caller untouched.

    The controller's background coroutine has an explicit re-raise on
    ``MemoryError`` / ``RecursionError``; the adapter must not catch
    them either, or the never-give-up rule on resource exhaustion is
    silently broken.
    """
    pipeline = mock_of[WorkPipeline]()
    pipeline.run.side_effect = MemoryError("OOM")
    with pytest.raises(MemoryError):
        await _adapter(pipeline).submit(_filing())


async def test_submit_propagates_recursion_error() -> None:
    pipeline = mock_of[WorkPipeline]()
    pipeline.run.side_effect = RecursionError("stack overflow")
    with pytest.raises(RecursionError):
        await _adapter(pipeline).submit(_filing())


async def test_submit_propagates_generic_exception() -> None:
    """An unexpected pipeline failure surfaces to the caller.

    The background coroutine in the controller logs and swallows
    broad exceptions to keep the detached task safe; the adapter
    layer itself must not pre-empt that and propagates everything
    so the controller's logging seam sees the real type.
    """
    pipeline = mock_of[WorkPipeline]()
    pipeline.run.side_effect = ValueError("unexpected pipeline failure")
    with pytest.raises(ValueError, match="unexpected pipeline failure"):
        await _adapter(pipeline).submit(_filing())
