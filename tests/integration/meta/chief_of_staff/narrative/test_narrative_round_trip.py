"""Documentary-mode acceptance test under the simulation harness.

Drives the real :class:`ChiefOfStaffNarrator` (via its boot factory)
against a real :class:`DocsService` rooted at a pytest tmp dir: a fake
flight recorder and project brain supply the run's facts, a scripted
provider supplies the connective prose, and the test asserts the
generated ``run_narrative`` living doc commits to the docs branch and
reads back through the dashboard read path with the trustworthy
structured blocks intact. This is the end-to-end check the issue's
acceptance ("persisted with the project, an auditor can trust") demands
for the per-brief pipeline seam.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.core.enums import DocType, GitBackendType, Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.factory import build_docs_service
from synthorg.docs_engine.models import (
    DecisionBlock,
    LinkBlock,
    MetricBlock,
)
from synthorg.engine.workspace.git_backend import (
    GitBackendConfig,
    GitBackendDeps,
    build_git_backend,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.narrative.factory import (
    build_chief_of_staff_narrator,
)
from synthorg.observability.events.chief_of_staff import COS_NARRATIVE_GENERATED
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameAggregate,
    FlightRecorderFrameRepository,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    BrainSummary,
    Citation,
    CitationKind,
    DecisionPayload,
)
from synthorg.project_brain.service import ProjectBrainService
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.protocol import CompletionProvider
from tests._shared import FakeClock, mock_of
from tests.integration.docs_engine._workspace import InMemoryWorkspaceRepo
from tests.unit.api.fakes import FakeDocsRepository

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_PROJECT = NotBlankStr("proj-1")
_TASK = NotBlankStr("task-1")


async def _docs_service(tmp_path: Path) -> Any:
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
    backend = InMemoryBackend()
    await backend.connect()
    runtime = build_docs_service(
        repo=FakeDocsRepository(),
        workspace_service=workspace_service,
        git_backend=git_backend,
        memory_backend=backend,
        clock=FakeClock(start=_NOW),
    )
    return runtime.docs_service


def _task() -> Task:
    return Task(
        id=_TASK,
        title=NotBlankStr("Ship checkout"),
        description=NotBlankStr("Build the checkout flow end to end."),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=_PROJECT,
        created_by=NotBlankStr("manager"),
        assigned_to=NotBlankStr("agent-a"),
        status=TaskStatus.COMPLETED,
    )


def _frame(agent_id: str, turn_index: int) -> FlightRecorderFrame:
    return FlightRecorderFrame(
        execution_id=NotBlankStr("exec-1"),
        task_id=_TASK,
        agent_id=NotBlankStr(agent_id),
        turn_index=turn_index,
        timestamp=_NOW,
        tool_calls=("read", "write"),
        cost=0.5,
        status=TaskStatus.COMPLETED,
    )


def _aggregate() -> FlightRecorderFrameAggregate:
    return FlightRecorderFrameAggregate(
        total_cost=1.5,
        max_turn_index=3,
        latest_timestamp=_NOW,
        latest_execution_id=NotBlankStr("exec-1"),
    )


def _decision_summary() -> BrainSummary:
    return BrainSummary(
        project_id=_PROJECT,
        entry_id=NotBlankStr("dec-1"),
        revision=1,
        entry_kind=BrainEntryKind.DECISION,
        title=NotBlankStr("Adopt event-sourced ledger"),
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("agent-a"),
        recorded_at=_NOW,
    )


def _decision_entry() -> BrainEntry:
    return BrainEntry(
        entry_id=NotBlankStr("dec-1"),
        revision=1,
        project_id=_PROJECT,
        entry_kind=BrainEntryKind.DECISION,
        title=NotBlankStr("Adopt event-sourced ledger"),
        rationale=NotBlankStr("Auditability outweighs write amplification."),
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("agent-a"),
        recorded_at=_NOW,
        related_task_ids=(_TASK,),
        citations=(
            Citation(
                source_ref=NotBlankStr("dec-1"),
                source_kind=CitationKind.ENTRY,
            ),
        ),
        payload=DecisionPayload(decision_outcome=NotBlankStr("Event-sourced ledger")),
    )


def _prose_response() -> CompletionResponse:
    payload = json.dumps(
        {
            "summary": "The team shipped the checkout flow.",
            "decisions": "One architectural decision shaped the run.",
            "contributions": "A single agent carried the work.",
            "outcomes": "The brief completed cleanly.",
        }
    )
    return CompletionResponse(
        content=payload,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=100, output_tokens=60, cost=0.002),
        model="example-small-001",
    )


class TestNarrativeRoundTrip:
    async def test_narrative_persists_and_reads_back(self, tmp_path: Path) -> None:
        docs_service = await _docs_service(tmp_path)
        brain = mock_of[ProjectBrainService](
            list_current=AsyncMock(return_value=(_decision_summary(),)),
            get_current=AsyncMock(return_value=_decision_entry()),
        )
        run_frames = (_frame("agent-a", 1), _frame("agent-a", 2))
        frames = mock_of[FlightRecorderFrameRepository](
            get_aggregate=AsyncMock(return_value=_aggregate()),
            query=AsyncMock(side_effect=[run_frames, ()]),
        )
        task_repo = mock_of[TaskRepository](get=AsyncMock(return_value=_task()))
        narrator = build_chief_of_staff_narrator(
            ChiefOfStaffConfig(narrative_enabled=True),
            provider=mock_of[CompletionProvider](
                complete=AsyncMock(return_value=_prose_response())
            ),
            docs_service=docs_service,
            brain_service=brain,
            frames=frames,
            task_repo=task_repo,
        )
        assert narrator is not None

        with structlog.testing.capture_logs() as events:
            metadata = await narrator.generate(task_id=_TASK, project_id=_PROJECT)

        assert metadata is not None
        assert metadata.doc_type is DocType.RUN_NARRATIVE
        assert NotBlankStr("execution:exec-1") in metadata.tags
        assert any(e["event"] == COS_NARRATIVE_GENERATED for e in events)

        # Round-trips through the real dashboard read path (git tip).
        doc = await docs_service.read_doc(project_id=_PROJECT, slug=metadata.slug)
        decisions = [b for b in doc.body if isinstance(b, DecisionBlock)]
        assert any(b.decision == "Event-sourced ledger" for b in decisions)
        assert any(isinstance(b, MetricBlock) for b in doc.body)
        links = [b for b in doc.body if isinstance(b, LinkBlock)]
        assert any(b.url == "#brain-entry-dec-1" for b in links)

    async def test_disabled_narrator_is_not_built(self, tmp_path: Path) -> None:
        docs_service = await _docs_service(tmp_path)
        narrator = build_chief_of_staff_narrator(
            ChiefOfStaffConfig(),
            provider=mock_of[CompletionProvider](
                complete=AsyncMock(return_value=_prose_response())
            ),
            docs_service=docs_service,
            brain_service=mock_of[ProjectBrainService](),
            frames=mock_of[FlightRecorderFrameRepository](),
            task_repo=mock_of[TaskRepository](),
        )
        assert narrator is None
