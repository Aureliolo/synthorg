"""Unit coverage for :class:`ObjectiveEntryAdapter` and :class:`ObjectiveSubmission`.

The adapter is the thin high-altitude work-entry seam: it maps a
human-stated objective onto a :class:`WorkItem` with
``source=OBJECTIVE`` and hands it to the work pipeline spine. It owns
no persistence, no lifecycle, no decomposition logic.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.pipeline.entry.objective_adapter import (
    ObjectiveEntryAdapter,
    ObjectiveSubmission,
)
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

_DEFAULT_PROJECT = "objectives"


def _submission(**overrides: Any) -> ObjectiveSubmission:
    base: dict[str, Any] = {
        "submission_id": "obj-1",
        "title": "Ship the v0.8 release",
        "description": "Cut a stable v0.8 release with full release notes.",
        "requested_by": "human-operator",
    }
    base.update(overrides)
    return ObjectiveSubmission(**base)


def _result(work_item: WorkItem) -> WorkPipelineResult:
    """Build a minimal :class:`WorkPipelineResult` for adapter tests.

    Phases is intentionally a single-entry tuple: tests using this
    helper assert on top-level fields (verdict, execution_path,
    task_id, correlation_id) only and do not introspect per-phase
    timing. If a future test inspects ``phases`` directly, populate
    the full intake-projects-decompose-route-execute-metrics tuple
    rather than extending this minimal stub.
    """
    return WorkPipelineResult(
        work_item=work_item,
        verdict=RoutingVerdict.SPLITTABLE,
        execution_path=ExecutionPath.TEAM,
        task_id="task-99",
        final_task_status=TaskStatus.IN_REVIEW,
        phases=(WorkPhaseResult(phase="intake", success=True, duration_seconds=0.0),),
        total_duration_seconds=0.2,
    )


def _adapter(pipeline: WorkPipeline) -> ObjectiveEntryAdapter:
    return ObjectiveEntryAdapter(
        work_pipeline=pipeline,
        default_project=_DEFAULT_PROJECT,
    )


def test_submission_is_frozen() -> None:
    submission = _submission()
    with pytest.raises(ValidationError):
        submission.title = "mutated"  # type: ignore[misc]


def test_submission_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ObjectiveSubmission(
            submission_id="obj-2",
            title="x",
            description="y",
            requested_by="z",
            unknown_field="x",  # type: ignore[call-arg]
        )


def test_submission_rejects_blank_strings() -> None:
    with pytest.raises(ValidationError):
        ObjectiveSubmission(
            submission_id="obj-3",
            title="   ",
            description="not blank",
            requested_by="human",
        )
    with pytest.raises(ValidationError):
        ObjectiveSubmission(
            submission_id="obj-4",
            title="not blank",
            description="   ",
            requested_by="human",
        )


def test_submission_rejects_blank_acceptance_criteria() -> None:
    """A blank-string entry in the acceptance_criteria tuple is rejected.

    ``acceptance_criteria`` is typed ``tuple[NotBlankStr, ...]``: the
    element annotation already enforces non-blank, but a regression
    test pins the contract so a future relaxation of the field type
    cannot silently let blank criteria through to the pipeline.
    """
    with pytest.raises(ValidationError):
        ObjectiveSubmission(
            title="t",
            description="d",
            requested_by="r",
            acceptance_criteria=("valid criterion", "   "),
        )


def test_submission_id_defaults_to_uuid() -> None:
    one = ObjectiveSubmission(
        title="t",
        description="d",
        requested_by="r",
    )
    two = ObjectiveSubmission(
        title="t",
        description="d",
        requested_by="r",
    )
    assert one.submission_id != two.submission_id
    assert len(one.submission_id) >= 32


def test_submission_accepts_full_optional_fields() -> None:
    submission = _submission(
        priority=Priority.CRITICAL,
        estimated_complexity=Complexity.EPIC,
        task_type=TaskType.RESEARCH,
        acceptance_criteria=("metric-X up 10%", "no regressions"),
    )
    assert submission.priority is Priority.CRITICAL
    assert submission.estimated_complexity is Complexity.EPIC
    assert submission.task_type is TaskType.RESEARCH
    assert submission.acceptance_criteria == ("metric-X up 10%", "no regressions")


def test_is_work_entry_adapter() -> None:
    adapter = _adapter(mock_of[WorkPipeline]())
    assert isinstance(adapter, WorkEntryAdapter)
    assert adapter.source is WorkSource.OBJECTIVE


async def test_submit_maps_submission_to_work_item() -> None:
    pipeline = mock_of[WorkPipeline]()
    captured: dict[str, WorkItem] = {}

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        captured["item"] = work_item
        return _result(work_item)

    pipeline.run.side_effect = _run
    submission = _submission(
        priority=Priority.HIGH,
        estimated_complexity=Complexity.EPIC,
        task_type=TaskType.RESEARCH,
        acceptance_criteria=("does X", "does Y"),
    )

    result = await _adapter(pipeline).submit(submission)

    item = captured["item"]
    assert item.source is WorkSource.OBJECTIVE
    assert item.correlation_id == submission.submission_id
    assert item.project == _DEFAULT_PROJECT
    assert item.title == submission.title
    assert item.raw_intent == submission.description
    assert item.requested_by == submission.requested_by
    assert item.priority is Priority.HIGH
    assert item.task_type is TaskType.RESEARCH
    assert item.estimated_complexity is Complexity.EPIC
    assert item.acceptance_criteria == submission.acceptance_criteria
    assert result.task_id == "task-99"


async def test_submit_uses_workitem_defaults_when_unspecified() -> None:
    """Optional submission fields fall through to WorkItem defaults."""
    pipeline = mock_of[WorkPipeline]()
    captured: dict[str, WorkItem] = {}

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        captured["item"] = work_item
        return _result(work_item)

    pipeline.run.side_effect = _run

    await _adapter(pipeline).submit(_submission())

    item = captured["item"]
    assert item.priority is Priority.MEDIUM
    assert item.task_type is TaskType.DEVELOPMENT
    assert item.estimated_complexity is Complexity.MEDIUM
    assert item.acceptance_criteria == ()


async def test_submit_files_into_default_project_regardless_of_submission() -> None:
    """The configured boot-time default project is authoritative."""
    pipeline = mock_of[WorkPipeline]()
    captured: dict[str, WorkItem] = {}

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        captured["item"] = work_item
        return _result(work_item)

    pipeline.run.side_effect = _run
    adapter = ObjectiveEntryAdapter(
        work_pipeline=pipeline,
        default_project="custom-objectives",
    )

    await adapter.submit(_submission())

    assert captured["item"].project == "custom-objectives"


async def test_submit_propagates_pipeline_error() -> None:
    pipeline = mock_of[WorkPipeline]()
    pipeline.run.side_effect = WorkIntakeRejectedError("nope")
    with pytest.raises(WorkIntakeRejectedError):
        await _adapter(pipeline).submit(_submission())
