"""E2E acceptance: a completed deliverable carries a valid provenance receipt.

Under the live engine harness an agent runs once (cassette RECORD):
it searches the knowledge corpus, runs a test command, and writes a
deliverable living document. The run's flight-recorder frames are
persisted, then the human-approval path (``ReviewGateService`` ->
COMPLETED) fires the receipt build.

The acceptance proof has three legs:

1. **The execution-id join closes** -- the records the capture sinks
   wrote under the run's ``execution_id`` contextvar carry the *same*
   id the flight recorder reports, so the builder joins on one key.
2. **The receipt is built, persisted, and self-validates** -- sources
   resolve against the knowledge registry, the claimed test matches a
   persisted code-execution record, and the cassette hashes.
3. **The cassette replays** -- every recorded provider interaction
   replays to its recorded outcome through a REPLAY session with no
   real driver constructed, so the cassette the receipt references
   genuinely serves the run's provider calls with zero real spend. (A
   byte-identical full agent re-run is not asserted: ``write_living_doc``
   echoes the embedded-git commit sha into the agent's context and that
   backend stamps commits from wall-clock, so a second run's later
   request hashes necessarily differ. Replaying the recorded
   interactions directly is the deterministic proof.)

``TaskEngine`` is a typed mock (the same seam the review-gate
integration tests use): the seam-to-engine sync is unit-tested in
``tests/unit/engine/test_review_gate.py``; here the focus is the real
capture -> build -> validate path the rest of the stack drives.
"""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.core.agent import ToolPermissions
from synthorg.core.enums import (
    GitBackendType,
    SourceStatus,
    SourceType,
    TaskStatus,
)
from synthorg.core.task import Task
from synthorg.deliverable_receipts.factory import build_deliverable_receipt_service
from synthorg.docs_engine.factory import build_docs_service
from synthorg.docs_engine.service import DocsService
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.flight_recording.sink import PersistenceFlightRecorderSink
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.engine.workspace.git_backend import (
    GitBackendConfig,
    GitBackendDeps,
    build_git_backend,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.indexer import KnowledgeIndexer
from synthorg.knowledge.models import (
    Citation,
    CodeLocator,
    KnowledgeHit,
    KnowledgeSource,
)
from synthorg.knowledge.retrieval import KnowledgeRetriever
from synthorg.knowledge.service import KnowledgeService
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.persistence.code_execution_protocol import CodeExecutionFilterSpec
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptFilterSpec,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
)
from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository
from synthorg.persistence.knowledge_usage_protocol import KnowledgeUsageFilterSpec
from synthorg.providers.cassette.mode import CassetteConfig, CassetteMode
from synthorg.providers.cassette.provider import CassetteCompletionProvider
from synthorg.providers.cassette.redaction import PatternRedactor
from synthorg.providers.cassette.store import (
    CassetteDocument,
    CassetteOutcomeKind,
    CassetteSession,
)
from synthorg.providers.drivers.scripted import (
    ScriptedDriver,
    SequencedResponseStrategy,
)
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage, ToolCall
from synthorg.tools.code_runner import CodeRunnerTool
from synthorg.tools.docs.write_living_doc import WriteLivingDocTool
from synthorg.tools.knowledge.search_knowledge import SearchKnowledgeTool
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import mock_of
from tests._shared.fake_clock import FakeClock
from tests.integration.docs_engine._workspace import InMemoryWorkspaceRepo
from tests.unit.api.fakes import FakePersistenceBackend

from .conftest import make_e2e_identity, make_e2e_task

pytestmark = pytest.mark.e2e

_PROVIDER = "receipt-e2e-provider"
_SOURCE_ID = "src-auth-spec"
_CONTENT_HASH = "a" * 64
_PROJECT = "proj-e2e"


def _knowledge_source() -> KnowledgeSource:
    return KnowledgeSource(
        source_id=_SOURCE_ID,
        source_type=SourceType.REPO,
        uri="repo://auth/spec.md",
        title="Auth spec",
        content_hash=_CONTENT_HASH,
        status=SourceStatus.INDEXED,
        chunk_count=1,
        created_at=FakeClock().now(),
        updated_at=FakeClock().now(),
        last_indexed_at=FakeClock().now(),
    )


def _hit() -> KnowledgeHit:
    return KnowledgeHit(
        chunk_text="The auth module must expose a login endpoint.",
        relevance_score=0.95,
        citation=Citation(
            source_id=_SOURCE_ID,
            chunk_id="chunk-1",
            source_type=SourceType.REPO,
            title="Auth spec",
            uri="repo://auth/spec.md",
            locator=CodeLocator(path="auth/spec.md", line_start=1, line_end=4),
            content_hash=_CONTENT_HASH,
        ),
    )


def _knowledge_service(persistence: FakePersistenceBackend) -> KnowledgeService:
    """A KnowledgeService whose retriever returns one fixed, resolvable hit."""
    retriever = mock_of[KnowledgeRetriever]()
    retriever.search = AsyncMock(return_value=(_hit(),))
    return KnowledgeService(
        sources=mock_of[KnowledgeSourceRepository](),
        indexer=mock_of[KnowledgeIndexer](),
        retriever=retriever,
        config=mock_of[KnowledgeConfig](),
        usage_records=persistence.knowledge_usage_records,
        clock=FakeClock(),
    )


def _sandbox() -> SandboxBackend:
    backend = mock_of[SandboxBackend]()
    backend.execute = AsyncMock(
        return_value=SandboxResult(
            stdout="5 passed",
            stderr="",
            returncode=0,
            timed_out=False,
        ),
    )
    return cast("SandboxBackend", backend)


async def _build_docs_service(
    persistence: FakePersistenceBackend,
    tmp_path: Path,
) -> DocsService:
    """Assemble a real (embedded-git, in-memory) docs service.

    Crucially backed by ``persistence.project_docs`` so a doc the agent
    writes is the same row the receipt service later scans.

    Returns:
        A wired :class:`DocsService` over an embedded git backend.
    """
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
    memory_backend = InMemoryBackend()
    await memory_backend.connect()
    runtime = build_docs_service(
        repo=persistence.project_docs,
        workspace_service=workspace_service,
        git_backend=git_backend,
        memory_backend=memory_backend,
        clock=FakeClock(),
    )
    return runtime.docs_service


def _scripted_run() -> tuple[CompletionResponse, ...]:
    """One tool-use turn (search, test, write deliverable) then complete."""
    return (
        CompletionResponse(
            tool_calls=(
                ToolCall(
                    id="call-search",
                    name="search_knowledge",
                    arguments={"query": "auth login endpoint", "limit": 5},
                ),
                ToolCall(
                    id="call-tests",
                    name="code_runner",
                    arguments={
                        "code": "pytest -q",
                        "language": "bash",
                        "purpose": "tests",
                    },
                ),
                ToolCall(
                    id="call-doc",
                    name="write_living_doc",
                    arguments={
                        "title": "Auth module deliverable",
                        "doc_type": "deliverable",
                        "body": [
                            {
                                "block_kind": "prose",
                                "text": "Implemented the login endpoint per spec.",
                            },
                        ],
                        "tags": [],
                        "related_task_ids": [],
                        "slug": None,
                    },
                ),
            ),
            finish_reason=FinishReason.TOOL_USE,
            usage=TokenUsage(input_tokens=40, output_tokens=12, cost=0.004),
            model="m",
        ),
        CompletionResponse(
            content="Deliverable complete.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=60, output_tokens=20, cost=0.006),
            model="m",
        ),
    )


def _registry(
    *,
    knowledge_service: KnowledgeService,
    docs_service: DocsService,
    persistence: FakePersistenceBackend,
) -> ToolRegistry:
    return ToolRegistry(
        [
            SearchKnowledgeTool(service=knowledge_service, project_id=_PROJECT),
            CodeRunnerTool(
                sandbox=_sandbox(),
                code_execution_records=persistence.code_execution_records,
            ),
            WriteLivingDocTool(
                docs_service=docs_service,
                project_id=_PROJECT,
                author_agent_id="agent-e2e",
            ),
        ]
    )


def _mock_task_engine_returning(task: Task) -> TaskEngine:
    """A TaskEngine seam that reports ``task`` IN_REVIEW and accepts the sync.

    Returns:
        A typed ``TaskEngine`` mock whose ``get_task`` yields ``task``.
    """
    engine = mock_of[TaskEngine](
        submit=AsyncMock(
            return_value=TaskMutationResult(
                request_id="req",
                success=True,
                version=1,
            ),
        ),
        get_task=AsyncMock(return_value=task),
    )
    return cast("TaskEngine", engine)


class TestDeliverableReceiptAcceptance:
    """Full provenance-receipt acceptance under the live engine harness."""

    async def test_completed_deliverable_carries_valid_receipt(
        self, tmp_path: Path
    ) -> None:
        persistence = FakePersistenceBackend()
        await persistence.connect()
        await persistence.knowledge_sources.save(_knowledge_source())

        docs_service = await _build_docs_service(persistence, tmp_path)
        knowledge_service = _knowledge_service(persistence)
        registry = _registry(
            knowledge_service=knowledge_service,
            docs_service=docs_service,
            persistence=persistence,
        )

        cassette_path = tmp_path / "receipt_run.cassette.json"
        session = CassetteSession(
            mode=CassetteMode.RECORD,
            path=cassette_path,
            redactor=PatternRedactor(),
        )
        provider = CassetteCompletionProvider(
            inner=ScriptedDriver(
                _PROVIDER,
                strategy=SequencedResponseStrategy(_scripted_run()),
            ),
            session=session,
            provider_name=_PROVIDER,
        )

        # write_living_doc is ToolCategory.OTHER, which STANDARD does not
        # grant; an explicit allow lets this deliverable-producing agent
        # write the doc the receipt anchors on.
        identity = make_e2e_identity(
            tools=ToolPermissions(allowed=("write_living_doc",)),
        )
        task = make_e2e_task(
            identity=identity,
            title="Auth module",
            description="Build the auth login endpoint per spec.",
        )
        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
            cost_tracker=CostTracker(),
            flight_recorder_sink=PersistenceFlightRecorderSink(
                persistence.flight_recorder_frames,
            ),
        )
        result = await engine.run(identity=identity, task=task, max_turns=5)
        await session.flush()

        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED
        execution_id = result.execution_result.context.execution_id

        # -- Leg 1: the execution-id join closes. --------------------
        aggregate = await persistence.flight_recorder_frames.get_aggregate(
            FlightRecorderFrameFilterSpec(task_id=task.id),
        )
        assert aggregate.latest_execution_id == execution_id

        usage_rows = await persistence.knowledge_usage_records.query(
            KnowledgeUsageFilterSpec(execution_id=execution_id),
        )
        assert {r.source_id for r in usage_rows} == {_SOURCE_ID}
        code_rows = await persistence.code_execution_records.query(
            CodeExecutionFilterSpec(execution_id=execution_id),
        )
        assert len(code_rows) == 1
        assert code_rows[0].passed is True

        # -- Leg 2: approve via the human path; receipt is built. ----
        receipt_service = build_deliverable_receipt_service(
            persistence=persistence,
            docs_service=docs_service,
            clock=FakeClock(),
            default_currency="USD",
            cassette_config=CassetteConfig(
                mode=CassetteMode.RECORD,
                path=cassette_path,
            ),
        )
        in_review = task.model_copy(update={"status": TaskStatus.IN_REVIEW})
        review_gate = ReviewGateService(
            task_engine=_mock_task_engine_returning(in_review),
            persistence=persistence,
            receipt_service=receipt_service,
        )
        await review_gate.complete_review(
            task_id=task.id,
            requested_by="reviewer-agent",
            approved=True,
            decided_by="reviewer-agent",
        )

        receipts = await persistence.deliverable_receipts.query(
            DeliverableReceiptFilterSpec(project_id=_PROJECT, task_id=task.id),
            limit=1,
        )
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.execution_id == execution_id
        assert {s.source_id for s in receipt.sources} == {_SOURCE_ID}
        assert len(receipt.tests) == 1
        assert receipt.tests[0].passed is True
        assert receipt.cassette is not None

        # Receipt self-validates: sources resolve, tests reconcile.
        validation = await receipt_service.validate(
            project_id=_PROJECT,
            slug=receipt.deliverable_doc_slug,
        )
        assert validation.valid is True
        assert validation.errors == ()

        # -- Leg 3: the cassette genuinely replays the run. ----------
        # A fresh REPLAY session serves every recorded interaction from the
        # cassette via ``take`` -- the exact path the cassette provider uses
        # in replay -- with no inner driver constructed at all, so "zero
        # real provider calls on replay" is structural rather than
        # best-effort. Both recorded turns replay to their recorded
        # successful completions.
        recorded = CassetteDocument.model_validate_json(
            cassette_path.read_text(encoding="utf-8")
        )
        assert len(recorded.interactions) == 2
        replay_session = CassetteSession(
            mode=CassetteMode.REPLAY,
            path=cassette_path,
            redactor=PatternRedactor(),
        )
        for interaction in recorded.interactions:
            replayed = replay_session.take(request_hash=interaction.request_hash)
            assert replayed.kind is CassetteOutcomeKind.RESPONSE
            assert replayed == interaction.outcome

        await persistence.disconnect()
