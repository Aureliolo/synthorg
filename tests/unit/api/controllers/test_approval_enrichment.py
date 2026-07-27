"""Tests for batched, best-effort approval read-time enrichment."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.approvals._enrichment import (
    build_approval_contexts,
    resolve_approval_context,
)
from synthorg.api.controllers.approvals._shared import (
    ApprovalAgentRef,
    ApprovalArtifactRef,
    ApprovalContext,
    ApprovalProjectRef,
    ApprovalRunSummary,
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
from tests._shared import as_uuid, make_app_state, sid

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


async def _resolve(
    items: Sequence[ApprovalItem],
    *,
    tasks: dict[str, Task] | None = None,
    projects: dict[str, Project] | None = None,
    artifacts: dict[str, tuple[Artifact, ...]] | None = None,
    agent_name_by_id: dict[str, str] | None = None,
    task_calls: list[str] | None = None,
    raise_tasks: set[str] | None = None,
    raise_artifacts: set[str] | None = None,
) -> dict[str, ApprovalContext]:
    tasks = tasks or {}
    projects = projects or {}
    artifacts = artifacts or {}

    async def get_task(tid: str) -> Task | None:
        if task_calls is not None:
            task_calls.append(tid)
        if raise_tasks is not None and tid in raise_tasks:
            msg = "task backend down"
            raise RuntimeError(msg)
        return tasks.get(tid)

    async def get_project(pid: str) -> Project | None:
        return projects.get(pid)

    async def list_artifacts(tid: str) -> Sequence[Artifact]:
        if raise_artifacts is not None and tid in raise_artifacts:
            msg = "artifact backend down"
            raise RuntimeError(msg)
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

    async def test_in_progress_task_has_no_run_summary(self) -> None:
        # A run that has not finished shows no produced-output badge.
        tid = sid("task-live")
        item = _item("appr-live", task_id=tid)
        task = _task(
            tid, project=sid("p"), status=TaskStatus.IN_PROGRESS, stakes=Stakes.LOW
        )
        contexts = await _resolve([item], tasks={tid: task})
        ctx = contexts[str(item.id)]
        assert ctx.task is not None
        assert ctx.run is None

    async def test_artifact_failure_leaves_run_unknown_not_empty(self) -> None:
        # A failed artifact listing must not be laundered into a truthful-
        # looking "produced nothing" (EMPTY); the outcome is unknown (None).
        tid = sid("task-io")
        item = _item("appr-io", task_id=tid)
        task = _task(
            tid, project=sid("p"), status=TaskStatus.IN_REVIEW, stakes=Stakes.LOW
        )
        contexts = await _resolve([item], tasks={tid: task}, raise_artifacts={tid})
        ctx = contexts[str(item.id)]
        assert ctx.task is not None
        assert ctx.run is None

    async def test_failed_task_summarised_even_when_artifacts_unavailable(self) -> None:
        # A failure is known from the status, so it is still surfaced when the
        # artifact listing is unavailable.
        tid = sid("task-failio")
        item = _item("appr-failio", task_id=tid)
        task = _task(
            tid, project=sid("p"), status=TaskStatus.FAILED, stakes=Stakes.HIGH
        )
        contexts = await _resolve([item], tasks={tid: task}, raise_artifacts={tid})
        ctx = contexts[str(item.id)]
        assert ctx.run is not None
        assert ctx.run.outcome is RunOutcome.FAILED

    async def test_task_lookup_failure_keeps_agent(self) -> None:
        item = _item("appr-taskio", task_id=sid("boom"), requested_by="operator-jane")
        contexts = await _resolve([item], raise_tasks={sid("boom")})
        ctx = contexts[str(item.id)]
        assert ctx.task is None
        assert ctx.run is None
        assert ctx.agent is not None
        assert ctx.agent.name == "operator-jane"


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


class TestContextInvariants:
    def test_run_without_task_is_rejected(self) -> None:
        run = ApprovalRunSummary(outcome=RunOutcome.FAILED, produced_artifact_count=0)
        with pytest.raises(ValidationError):
            ApprovalContext(run=run)

    def test_project_without_task_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalContext(
                project=ApprovalProjectRef(id=sid("p"), name=NotBlankStr("P"))
            )

    def test_agent_only_context_is_valid(self) -> None:
        ctx = ApprovalContext(
            agent=ApprovalAgentRef(id=sid("a"), name=NotBlankStr("Agent"))
        )
        assert ctx.task is None

    def test_artifacts_cannot_exceed_produced_count(self) -> None:
        ref = ApprovalArtifactRef(
            id=NotBlankStr("art-1"),
            path=NotBlankStr("src/a.py"),
            type=ArtifactType.CODE,
            content_type="text/x-python",
            size_bytes=1,
        )
        with pytest.raises(ValidationError):
            ApprovalRunSummary(
                outcome=RunOutcome.SUCCEEDED,
                produced_artifact_count=0,
                artifacts=(ref,),
            )


class TestResolveApprovalContextWiring:
    async def test_backend_unwired_degrades_to_agent_only(self) -> None:
        # With no persistence backend, the context still carries the resolved
        # agent name (from config) rather than failing the queue.
        state = make_app_state(persistence=None)
        item = _item("appr-nb", task_id=sid("task-1"), requested_by=sid("agent-x"))
        contexts = await resolve_approval_context(state, [item])
        ctx = contexts[str(item.id)]
        assert ctx.task is None
        assert ctx.run is None
        assert ctx.agent is not None
        assert ctx.agent.id == sid("agent-x")

    async def test_empty_items_short_circuits(self) -> None:
        state = make_app_state(persistence=None)
        assert await resolve_approval_context(state, []) == {}
