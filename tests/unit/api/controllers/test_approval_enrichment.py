"""Tests for batched, best-effort approval read-time enrichment."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from synthorg.api.controllers.approvals._enrichment import build_approval_contexts
from synthorg.api.controllers.approvals._shared import (
    ApprovalContext,
    _to_approval_response,
)
from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.approval import ApprovalItem
from synthorg.core.artifact import Artifact, ArtifactType
from synthorg.core.project import Project
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _item(
    label: str, *, task_id: str | None, requested_by: str = "agent-x"
) -> ApprovalItem:
    return ApprovalItem(
        id=as_uuid(label),
        action_type="review:task_completion",
        title=f"Review task {task_id} completion",
        description="desc",
        requested_by=NotBlankStr(requested_by),
        risk_level=ApprovalRiskLevel.LOW,
        created_at=_NOW,
        task_id=NotBlankStr(task_id) if task_id is not None else None,
    )


def _task(task_id: str, *, project: str, status: TaskStatus, stakes: Stakes) -> Task:
    return Task(
        id=as_uuid(task_id),
        title="Ship the onboarding flow",
        description="desc",
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr(project),
        created_by=NotBlankStr("agent-x"),
        assigned_to=NotBlankStr("agent-x"),
        status=status,
        stakes=stakes,
    )


def _artifact(artifact_id: str, *, task_id: str) -> Artifact:
    return Artifact(
        id=NotBlankStr(artifact_id),
        type=ArtifactType.CODE,
        path=NotBlankStr(f"src/{artifact_id}.py"),
        task_id=NotBlankStr(task_id),
        created_by=NotBlankStr("agent-x"),
        content_type="text/x-python",
        size_bytes=42,
    )


async def _resolve(  # noqa: PLR0913 -- test fixture assembling injected fakes
    items: Sequence[ApprovalItem],
    *,
    tasks: dict[str, Task] | None = None,
    projects: dict[str, Project] | None = None,
    artifacts: dict[str, tuple[Artifact, ...]] | None = None,
    agent_name_by_id: dict[str, str] | None = None,
    task_calls: list[str] | None = None,
) -> dict[str, ApprovalContext]:
    tasks = tasks or {}
    projects = projects or {}
    artifacts = artifacts or {}

    async def get_task(tid: str) -> Task | None:
        if task_calls is not None:
            task_calls.append(tid)
        return tasks.get(tid)

    async def get_project(pid: str) -> Project | None:
        return projects.get(pid)

    async def list_artifacts(tid: str) -> Sequence[Artifact]:
        return artifacts.get(tid, ())

    return await build_approval_contexts(
        items,
        get_task=get_task,
        get_project=get_project,
        list_artifacts=list_artifacts,
        agent_name_by_id=agent_name_by_id or {},
    )


class TestBuildApprovalContexts:
    async def test_resolves_names_and_run_summary(self) -> None:
        tid = sid("task-1")
        pid = sid("proj-1")
        item = _item("appr-1", task_id=tid, requested_by=sid("agent-anica"))
        task = _task(tid, project=pid, status=TaskStatus.IN_REVIEW, stakes=Stakes.HIGH)
        project = Project(id=as_uuid("proj-1"), name=NotBlankStr("Onboarding"))
        contexts = await _resolve(
            [item],
            tasks={tid: task},
            projects={pid: project},
            artifacts={tid: (_artifact("artifact-a", task_id=tid),)},
            agent_name_by_id={sid("agent-anica"): "Anica Hocevar"},
        )
        ctx = contexts[str(item.id)]
        assert ctx.task is not None
        assert ctx.task.title == "Ship the onboarding flow"
        assert ctx.project is not None
        assert ctx.project.name == "Onboarding"
        assert ctx.agent is not None
        assert ctx.agent.name == "Anica Hocevar"
        assert ctx.run is not None
        assert ctx.run.outcome is RunOutcome.SUCCEEDED
        assert ctx.run.produced_artifact_count == 1

    async def test_empty_run_is_labelled_empty(self) -> None:
        tid = sid("task-2")
        item = _item("appr-2", task_id=tid)
        task = _task(
            tid, project=sid("p"), status=TaskStatus.IN_REVIEW, stakes=Stakes.LOW
        )
        contexts = await _resolve([item], tasks={tid: task}, artifacts={tid: ()})
        ctx = contexts[str(item.id)]
        assert ctx.run is not None
        assert ctx.run.outcome is RunOutcome.EMPTY
        assert ctx.run.produced_artifact_count == 0

    async def test_failed_task_run_outcome_failed(self) -> None:
        tid = sid("task-3")
        item = _item("appr-3", task_id=tid)
        task = _task(
            tid, project=sid("p"), status=TaskStatus.FAILED, stakes=Stakes.HIGH
        )
        contexts = await _resolve([item], tasks={tid: task})
        ctx = contexts[str(item.id)]
        assert ctx.run is not None
        assert ctx.run.outcome is RunOutcome.FAILED

    async def test_agent_name_falls_back_to_id(self) -> None:
        item = _item("appr-4", task_id=None, requested_by="operator-jane")
        contexts = await _resolve([item])
        ctx = contexts[str(item.id)]
        assert ctx.agent is not None
        assert ctx.agent.name == "operator-jane"
        assert ctx.task is None
        assert ctx.run is None

    async def test_batches_each_task_once(self) -> None:
        tid = sid("task-shared")
        items = [_item(f"appr-{n}", task_id=tid) for n in range(3)]
        task = _task(
            tid, project=sid("p"), status=TaskStatus.IN_REVIEW, stakes=Stakes.LOW
        )
        calls: list[str] = []
        await _resolve(items, tasks={tid: task}, task_calls=calls)
        assert calls == [tid]  # one lookup for the shared task, not three

    async def test_missing_task_leaves_task_none_but_keeps_agent(self) -> None:
        item = _item("appr-5", task_id=sid("gone"), requested_by=sid("agent-anica"))
        contexts = await _resolve(
            [item], agent_name_by_id={sid("agent-anica"): "Anica Hocevar"}
        )
        ctx = contexts[str(item.id)]
        assert ctx.task is None
        assert ctx.agent is not None
        assert ctx.agent.name == "Anica Hocevar"


class TestToApprovalResponseMapping:
    def test_context_maps_onto_response_fields(self) -> None:
        item = _item("appr-map", task_id=sid("t"))
        context = ApprovalContext()
        resp = _to_approval_response(
            item,
            now=_NOW,
            urgency_critical_seconds=60.0,
            urgency_high_seconds=120.0,
            context=context,
        )
        assert resp.task is None
        assert resp.run is None

    async def test_full_context_flows_through(self) -> None:
        tid = sid("t")
        item = _item("appr-map2", task_id=tid)
        task = _task(
            tid, project=sid("p"), status=TaskStatus.IN_REVIEW, stakes=Stakes.LOW
        )
        contexts = await _resolve([item], tasks={tid: task}, artifacts={tid: ()})
        resp = _to_approval_response(
            item,
            now=_NOW,
            urgency_critical_seconds=60.0,
            urgency_high_seconds=120.0,
            context=contexts[str(item.id)],
        )
        assert resp.task is not None
        assert resp.run is not None
        assert resp.run.outcome is RunOutcome.EMPTY
