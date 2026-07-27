"""Unit tests for the Chief of Staff org-state read model."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.meta.chief_of_staff.models import CitedRecord
from synthorg.meta.chief_of_staff.org_state import (
    OrgStateReader,
    OrgStateSnapshot,
    ProjectDigest,
    TaskDigest,
    cited_records,
    format_org_state,
)
from synthorg.persistence.project_protocol import (
    ProjectFilterSpec,
    ProjectRepository,
)
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from tests._shared import FakeClock, mock_of, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)


def _task(title: str, status: TaskStatus, *, assignee: str | None = "agent-1") -> Task:
    return Task(
        title=title,
        description=f"{title} body",
        type=TaskType.DEVELOPMENT,
        project=sid("proj-platform"),
        created_by=sid("planner"),
        assigned_to=sid(assignee) if assignee is not None else None,
        status=status,
    )


def _project(
    name: str,
    status: ProjectStatus = ProjectStatus.ACTIVE,
    *,
    lead: str | None = "lead-1",
) -> Project:
    resolved_lead = sid(lead) if lead is not None else None
    return Project(name=name, status=status, lead=resolved_lead)


def _approval(title: str) -> ApprovalItem:
    return ApprovalItem(
        action_type="hiring.request",
        title=title,
        description=f"{title} detail",
        requested_by=sid("hr_agent"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
    )


def _reader(
    *,
    in_progress: tuple[Task, ...] = (),
    in_progress_total: int | None = None,
    in_review: tuple[Task, ...] = (),
    in_review_total: int | None = None,
    projects: tuple[Project, ...] = (),
    projects_total: int | None = None,
    approvals: tuple[ApprovalItem, ...] = (),
    max_items: int = 10,
) -> OrgStateReader:
    ip_total = in_progress_total if in_progress_total is not None else len(in_progress)
    ir_total = in_review_total if in_review_total is not None else len(in_review)
    proj_total = projects_total if projects_total is not None else len(projects)
    task_repo = mock_of[TaskRepository](
        query=AsyncMock(side_effect=[in_progress, in_review]),
        count=AsyncMock(side_effect=[ip_total, ir_total]),
    )
    project_repo = mock_of[ProjectRepository](
        query=AsyncMock(return_value=projects),
        count=AsyncMock(return_value=proj_total),
    )
    store = mock_of[ApprovalStoreProtocol](
        list_items=AsyncMock(return_value=approvals),
    )
    return OrgStateReader(
        task_repo=task_repo,
        project_repo=project_repo,
        approval_store=store,
        max_items_per_section=max_items,
        clock=FakeClock(start=_NOW),
    )


class TestOrgStateReader:
    """OrgStateReader.read tests."""

    async def test_reads_all_four_surfaces_with_correct_filters(self) -> None:
        reader = _reader(
            in_progress=(_task("Fix login", TaskStatus.IN_PROGRESS),),
            in_review=(_task("Ship API", TaskStatus.IN_REVIEW),),
            projects=(_project("Platform"),),
            approvals=(_approval("Hire SRE"),),
        )
        state = await reader.read()

        assert len(state.in_progress_tasks) == 1
        assert state.in_progress_tasks[0].title == "Fix login"
        assert state.in_progress_tasks[0].status is TaskStatus.IN_PROGRESS
        assert state.in_review_tasks[0].status is TaskStatus.IN_REVIEW
        assert state.active_projects[0].name == "Platform"
        assert state.pending_approvals[0].title == "Hire SRE"
        assert state.read_at == _NOW
        assert state.has_work is True

    async def test_filter_specs_target_the_right_statuses(self) -> None:
        # Key the task fake by the status arg (not call order) so a
        # create_task reorder in the concurrent fan-out cannot silently swap
        # the in-progress / in-review results between sections.
        by_status = {
            TaskStatus.IN_PROGRESS: (_task("A", TaskStatus.IN_PROGRESS),),
            TaskStatus.IN_REVIEW: (_task("B", TaskStatus.IN_REVIEW),),
        }

        async def _task_query(spec: TaskFilterSpec, *, limit: int) -> tuple[Task, ...]:
            assert spec.status is not None
            return by_status[spec.status][:limit]

        task_query = AsyncMock(side_effect=_task_query)
        project_query = AsyncMock(return_value=(_project("P"),))
        list_items = AsyncMock(return_value=())
        reader = OrgStateReader(
            task_repo=mock_of[TaskRepository](
                query=task_query, count=AsyncMock(return_value=1)
            ),
            project_repo=mock_of[ProjectRepository](
                query=project_query, count=AsyncMock(return_value=1)
            ),
            approval_store=mock_of[ApprovalStoreProtocol](list_items=list_items),
            max_items_per_section=7,
            clock=FakeClock(start=_NOW),
        )
        await reader.read()

        query_specs = {call.args[0].status for call in task_query.call_args_list}
        assert query_specs == {TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW}
        # The configured per-section cap is threaded into the repo query.
        assert task_query.call_args_list[0].kwargs["limit"] == 7
        assert project_query.call_args.kwargs["limit"] == 7
        assert project_query.call_args.args[0] == ProjectFilterSpec(
            status=ProjectStatus.ACTIVE
        )
        assert list_items.call_args.kwargs["status"] is ApprovalStatus.PENDING

    async def test_totals_reflect_count_when_truncated(self) -> None:
        reader = _reader(
            in_progress=(_task("A", TaskStatus.IN_PROGRESS),),
            in_progress_total=7,
            approvals=tuple(_approval(f"Req {i}") for i in range(5)),
            max_items=2,
        )
        state = await reader.read()

        assert len(state.in_progress_tasks) == 1
        assert state.in_progress_total == 7
        # Approvals are sliced to the section cap; the total keeps the full count.
        assert len(state.pending_approvals) == 2
        assert state.pending_approvals_total == 5

    async def test_no_work_when_all_surfaces_empty(self) -> None:
        state = await _reader().read()
        assert state.has_work is False
        assert state.in_progress_total == 0
        assert state.pending_approvals_total == 0

    async def test_pending_approvals_alone_are_not_work(self) -> None:
        # Pending approvals are queued decisions, not active work: has_work
        # must stay False when only the approval queue is populated.
        state = await _reader(approvals=(_approval("Hire SRE"),)).read()
        assert state.pending_approvals_total == 1
        assert state.has_work is False

    async def test_count_below_sample_is_clamped(self) -> None:
        # A query/count read race can leave the count trailing the sample;
        # the reader clamps the total up so it never drops below the rows it
        # actually returned (which would hide the truncation note).
        reader = _reader(
            in_progress=(_task("A", TaskStatus.IN_PROGRESS),),
            in_progress_total=0,
        )
        state = await reader.read()
        assert len(state.in_progress_tasks) == 1
        assert state.in_progress_total == 1

    async def test_read_error_propagates_first_leaf(self) -> None:
        # The TaskGroup wraps a child fault in an ExceptionGroup; read()
        # unwraps it to the first leaf so the caller's typed-error handling
        # sees the original error, not an opaque group.
        task_repo = mock_of[TaskRepository](
            query=AsyncMock(side_effect=RuntimeError("db down")),
            count=AsyncMock(return_value=0),
        )
        project_repo = mock_of[ProjectRepository](
            query=AsyncMock(return_value=()),
            count=AsyncMock(return_value=0),
        )
        store = mock_of[ApprovalStoreProtocol](list_items=AsyncMock(return_value=()))
        reader = OrgStateReader(
            task_repo=task_repo,
            project_repo=project_repo,
            approval_store=store,
            max_items_per_section=10,
            clock=FakeClock(start=_NOW),
        )
        with pytest.raises(RuntimeError, match="db down"):
            await reader.read()


class TestFormatOrgState:
    """format_org_state rendering tests."""

    async def test_renders_work_with_citations(self) -> None:
        state = await _reader(
            in_progress=(_task("Fix login", TaskStatus.IN_PROGRESS),),
            in_review=(_task("Ship API", TaskStatus.IN_REVIEW),),
            projects=(_project("Platform"),),
            approvals=(_approval("Hire SRE"),),
        ).read()
        text = format_org_state(state)
        assert "Fix login" in text
        assert "Ship API" in text
        assert "Platform" in text
        assert "Hire SRE" in text

    async def test_truncation_line_when_capped(self) -> None:
        state = await _reader(
            approvals=tuple(_approval(f"Req {i}") for i in range(5)),
            max_items=2,
        ).read()
        text = format_org_state(state)
        assert "2 of 5" in text

    async def test_empty_sections_render_none(self) -> None:
        text = format_org_state(await _reader().read())
        assert "none" in text.lower()

    def test_unassigned_task_and_project_render(self) -> None:
        # A digest can carry no assignee/lead even though an in-progress Task
        # entity must; build the snapshot directly to exercise the "unassigned"
        # rendering branch without a contradictory entity.
        state = OrgStateSnapshot(
            in_progress_tasks=(
                TaskDigest(
                    task_id=sid("t1"),
                    title="Fix login",
                    status=TaskStatus.IN_PROGRESS,
                    project=sid("proj-platform"),
                    assigned_to=None,
                ),
            ),
            in_progress_total=1,
            active_projects=(
                ProjectDigest(
                    project_id=sid("p1"),
                    name="Platform",
                    status=ProjectStatus.ACTIVE,
                    lead=None,
                ),
            ),
            active_projects_total=1,
            read_at=_NOW,
        )
        text = format_org_state(state)
        assert "assigned to unassigned" in text
        assert "lead unassigned" in text


class TestCitedRecords:
    """cited_records mapping tests."""

    async def test_maps_each_surface(self) -> None:
        state = await _reader(
            in_progress=(_task("Fix login", TaskStatus.IN_PROGRESS),),
            projects=(_project("Platform"),),
            approvals=(_approval("Hire SRE"),),
        ).read()
        records = cited_records(state)
        kinds = {r.kind for r in records}
        assert kinds == {"task", "project", "approval"}
        task_ref = next(r for r in records if r.kind == "task")
        assert task_ref.label == "Fix login"
        assert task_ref.status == "in_progress"

    async def test_empty_state_cites_nothing(self) -> None:
        assert cited_records(await _reader().read()) == ()


class TestInvariants:
    """Cross-field construction invariants on the frozen models."""

    def test_snapshot_rejects_sample_exceeding_total(self) -> None:
        with pytest.raises(ValidationError, match="exceeds reported total"):
            OrgStateSnapshot(
                in_progress_tasks=(
                    TaskDigest(
                        task_id=sid("t1"),
                        title="A",
                        status=TaskStatus.IN_PROGRESS,
                        project=sid("p1"),
                    ),
                ),
                in_progress_total=0,
                read_at=_NOW,
            )

    def test_cited_record_rejects_status_not_valid_for_kind(self) -> None:
        with pytest.raises(ValidationError, match="not a valid status for kind"):
            CitedRecord(
                kind="task",
                record_id=sid("t1"),
                label="A",
                status="active",  # a ProjectStatus value, not a TaskStatus one
            )

    def test_cited_record_accepts_matching_status(self) -> None:
        record = CitedRecord(
            kind="project",
            record_id=sid("p1"),
            label="Platform",
            status="active",
        )
        assert record.status == "active"
