"""Acceptance: a run is redirected mid-flight and the agent continues.

Wires the real :class:`SteeringService` against the real project-brain engine
(chunker + indexer + writer + service over an embedded git repo + a real
``InMemoryBackend``), then drives a real :class:`ReactLoop` reading the real
:class:`BrainBackedSteeringInbox`. The operator issues a redirect with an
explicit supersede; the brain records the directive (SQL + git + RAG index), the
obsolete task is cancelled through the task engine, and both the in-flight agent
and a freshly-spawned agent adopt the directive at their next safe boundary with
no state corruption. This is the integration-tier counterpart to the runnable
unit proxy in ``tests/unit/engine/intervention/test_end_to_end.py``.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.enums import (
    GitBackendType,
    InterventionKind,
    Priority,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention import (
    NoOpSupersessionProposer,
    SteeringService,
    build_steering_inbox,
)
from synthorg.engine.intervention.models import SupersedeMode
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.react_loop import ReactLoop
from synthorg.engine.workspace.git_backend import (
    GitBackendConfig,
    GitBackendDeps,
    build_git_backend,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.project_brain.factory import build_project_brain_service
from synthorg.project_brain.models import BrainEntryKind
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse, TokenUsage
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity
from tests.integration.docs_engine._workspace import InMemoryWorkspaceRepo
from tests.unit.api.fakes import FakeProjectBrainRepository

pytestmark = pytest.mark.integration

_PROJECT = NotBlankStr("proj-steer")
_DIRECTIVE_TEXT = "use Postgres not Mongo"
_OBSOLETE_TASK = NotBlankStr("task-mongo-migration")


class _RecordingTaskEngine:
    """Records cancellations driven by the explicit supersede."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_task(
        self, task_id: str, *, requested_by: str, reason: str
    ) -> tuple[None, None]:
        self.cancelled.append(task_id)
        return (None, None)

    async def list_tasks(
        self, *, status: TaskStatus, project: str, limit: int
    ) -> tuple[tuple[object, ...], int]:
        return ((), 0)


def _stop() -> CompletionResponse:
    return CompletionResponse(
        content="Acknowledged.",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
        model="test-model-001",
    )


def _is_steering_msg(msg: ChatMessage) -> bool:
    return msg.role is MessageRole.USER and _DIRECTIVE_TEXT in (msg.content or "")


async def _build_brain_service(
    tmp_path: Path,
    repo: FakeProjectBrainRepository,
    backend: InMemoryBackend,
) -> object:
    """Wire the real project-brain service over an embedded git repo."""
    config = GitBackendConfig(kind=GitBackendType.EMBEDDED)
    git_backend = build_git_backend(
        config,
        GitBackendDeps(workspace_base_root=tmp_path, clock=FakeClock()),
    )
    workspace_service = ProjectWorkspaceService(
        base_root=tmp_path,
        repo=InMemoryWorkspaceRepo(),
        git_backend=git_backend,
        config=config,
        clock=FakeClock(),
    )
    runtime = build_project_brain_service(
        repo=repo,
        workspace_service=workspace_service,
        git_backend=git_backend,
        memory_backend=backend,
        clock=FakeClock(start=datetime(2026, 5, 31, tzinfo=UTC)),
    )
    return runtime.brain_service  # type: ignore[attr-defined]


def _ctx_for_run() -> AgentContext:
    identity = make_e2e_identity()
    task = Task(
        id="task-checkout",
        title="Wire the data layer",
        description="Stand up persistence for the checkout service.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=_PROJECT,
        created_by="pm",
        assigned_to=str(identity.id),
        status=TaskStatus.ASSIGNED,
    )
    ctx = AgentContext.from_identity(identity, task=task)
    return ctx.with_message(
        ChatMessage(role=MessageRole.USER, content="Build the data layer.")
    )


class TestMidFlightSteeringAcceptance:
    async def test_redirect_recorded_superseded_and_adopted(
        self, tmp_path: Path
    ) -> None:
        repo = FakeProjectBrainRepository()
        backend = InMemoryBackend()
        await backend.connect()
        try:
            brain_service = await _build_brain_service(tmp_path, repo, backend)
            engine = _RecordingTaskEngine()
            service = SteeringService(
                brain_service=brain_service,  # type: ignore[arg-type]
                brain_repo=repo,
                task_engine=engine,  # type: ignore[arg-type]
                proposer=NoOpSupersessionProposer(),
            )
            inbox = build_steering_inbox(repo)

            # 1. The operator redirects the project and supersedes the obsolete
            #    task. The real brain records it (SQL + git + RAG index).
            result = await service.issue(
                project_id=_PROJECT,
                kind=InterventionKind.REDIRECT,
                text=NotBlankStr(_DIRECTIVE_TEXT),
                author=NotBlankStr("mission-control"),
                supersede_task_ids=(_OBSOLETE_TASK,),
                supersede_mode=SupersedeMode.EXPLICIT,
            )
            assert engine.cancelled == [_OBSOLETE_TASK]

            # 2. The brain records the directive as a PLAN_REVISION tagged
            #    steering; the board projection carries the id + tags.
            recorded = await brain_service.list_current(  # type: ignore[attr-defined]
                project_id=_PROJECT,
                entry_kind=BrainEntryKind.PLAN_REVISION,
            )
            assert len(recorded) == 1
            summary = recorded[0]
            assert summary.entry_id == result.directive_id
            assert NotBlankStr("steering") in summary.tags
            # The full entry stores the operator's redirect text verbatim as
            # its rationale (the summary projection omits rationale).
            entry = await brain_service.get_entry(  # type: ignore[attr-defined]
                project_id=_PROJECT,
                entry_id=result.directive_id,
            )
            assert entry.rationale == _DIRECTIVE_TEXT
            assert entry.entry_kind is BrainEntryKind.PLAN_REVISION

            # 3. The in-flight agent adopts the directive at its turn boundary.
            run = await ReactLoop(steering_inbox=inbox).execute(
                context=_ctx_for_run(),
                provider=ScriptedProvider(responses=[_stop()]),
            )
            assert run.termination_reason is TerminationReason.COMPLETED
            assert [m for m in run.context.conversation if _is_steering_msg(m)]
            assert result.directive_id in run.context.adopted_steering_ids

            # 4. A freshly-spawned agent on the same project also adopts it
            #    before its first decision (every agent adopts).
            fresh = await ReactLoop(steering_inbox=inbox).execute(
                context=_ctx_for_run(),
                provider=ScriptedProvider(responses=[_stop()]),
            )
            assert result.directive_id in fresh.context.adopted_steering_ids
        finally:
            await backend.disconnect()
