"""Unit coverage for :class:`ObjectiveEntryAdapter` and :class:`ObjectiveSubmission`.

The adapter is the high-altitude work-entry seam: it maps a
human-stated objective onto a :class:`WorkItem` with
``source=OBJECTIVE`` and hands it to the work pipeline spine. Unlike
the intake adapter it owns a project repository: every objective is
its own initiative, so the adapter mints a dedicated project per
submission (idempotent by submission id) rather than filing into a
shared bucket.
"""

from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid5

import pytest
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.pipeline.entry.objective_adapter import (
    _PROJECT_NAMESPACE,
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
from synthorg.persistence.project_protocol import ProjectRepository
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _expected_project(submission_id: str) -> str:
    """Return the deterministic per-initiative project id for a submission."""
    return str(uuid5(_PROJECT_NAMESPACE, f"objective-{submission_id}"))


def _submission(**overrides: object) -> ObjectiveSubmission:
    base: dict[str, object] = {
        "submission_id": "obj-1",
        "title": "Ship the v0.8 release",
        "description": "Cut a stable v0.8 release with full release notes.",
        "requested_by": "human-operator",
    }
    base.update(overrides)
    return ObjectiveSubmission.model_validate(base)


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


def _repo(*, existing: Project | None = None) -> ProjectRepository:
    """Return a project repo whose ``get`` yields *existing* (default absent)."""
    repo = mock_of[ProjectRepository]()
    repo.get.return_value = existing
    repo.create.return_value = None
    return cast(ProjectRepository, repo)


def _adapter(
    pipeline: WorkPipeline,
    repo: ProjectRepository | None = None,
) -> ObjectiveEntryAdapter:
    return ObjectiveEntryAdapter(
        work_pipeline=pipeline,
        project_repo=repo if repo is not None else _repo(),
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
    # The auto-generated id must be a real (canonical) UUID, not just a
    # long string: parsing it and re-stringifying round-trips exactly.
    assert str(UUID(one.submission_id)) == one.submission_id
    assert str(UUID(two.submission_id)) == two.submission_id


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
    assert item.project == _expected_project(submission.submission_id)
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


async def test_submit_mints_per_initiative_project() -> None:
    """Each objective stands up its own PLANNING initiative project."""
    pipeline = mock_of[WorkPipeline]()
    pipeline.run.side_effect = _result
    repo = _repo()

    submission = _submission()
    await _adapter(pipeline, repo).submit(submission)

    create_mock = cast(AsyncMock, repo.create)
    create_mock.assert_awaited_once()
    assert create_mock.await_args is not None
    created: Project = create_mock.await_args.args[0]
    assert isinstance(created, Project)
    assert str(created.id) == _expected_project(submission.submission_id)
    assert created.name == submission.title
    assert created.status is ProjectStatus.PLANNING


async def test_submit_is_idempotent_when_project_exists() -> None:
    """A resubmitted objective reuses its project instead of re-creating."""
    pipeline = mock_of[WorkPipeline]()
    captured: dict[str, WorkItem] = {}

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        captured["item"] = work_item
        return _result(work_item)

    pipeline.run.side_effect = _run
    submission = _submission()
    existing = Project(
        id=uuid5(_PROJECT_NAMESPACE, f"objective-{submission.submission_id}"),
        name=submission.title,
        status=ProjectStatus.PLANNING,
    )
    repo = _repo(existing=existing)

    await _adapter(pipeline, repo).submit(submission)

    cast(AsyncMock, repo.create).assert_not_awaited()
    assert captured["item"].project == _expected_project(submission.submission_id)


async def test_submit_swallows_duplicate_record_on_create_race() -> None:
    """A concurrent winner racing on ``create`` is benign, not a failure.

    Both callers miss the ``get`` fast-path, race on ``create``, and the
    loser's ``DuplicateRecordError`` is swallowed: the project exists (the
    post-condition we want), so ``submit`` still succeeds and the work item
    routes into the deterministic per-initiative project.
    """
    pipeline = mock_of[WorkPipeline]()
    captured: dict[str, WorkItem] = {}

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        captured["item"] = work_item
        return _result(work_item)

    pipeline.run.side_effect = _run
    submission = _submission()
    repo = _repo()
    cast(AsyncMock, repo.create).side_effect = DuplicateRecordError("project exists")

    result = await _adapter(pipeline, repo).submit(submission)

    cast(AsyncMock, repo.create).assert_awaited_once()
    assert result.task_id == "task-99"
    assert captured["item"].project == _expected_project(submission.submission_id)


async def test_submit_files_distinct_objectives_into_distinct_projects() -> None:
    """Two different objectives never collide in one shared project."""
    pipeline = mock_of[WorkPipeline]()
    seen: list[str] = []

    async def _run(work_item: WorkItem) -> WorkPipelineResult:
        seen.append(work_item.project)
        return _result(work_item)

    pipeline.run.side_effect = _run
    adapter = _adapter(pipeline)

    await adapter.submit(_submission(submission_id="obj-a"))
    await adapter.submit(_submission(submission_id="obj-b"))

    assert seen[0] != seen[1]
    assert seen[0] == _expected_project("obj-a")
    assert seen[1] == _expected_project("obj-b")


async def test_submit_propagates_pipeline_error() -> None:
    pipeline = mock_of[WorkPipeline]()
    pipeline.run.side_effect = WorkIntakeRejectedError("nope")
    with pytest.raises(WorkIntakeRejectedError):
        await _adapter(pipeline).submit(_submission())
