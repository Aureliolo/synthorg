"""Unit tests for the provenance capture sinks (knowledge + code runner)."""

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.execution_identity import (
    ExecutionIdentity,
    execution_identity_scope,
)
from synthorg.knowledge.enums import SourceType
from synthorg.knowledge.models import Citation, CodeLocator, KnowledgeHit
from synthorg.knowledge.service import KnowledgeService
from synthorg.persistence.code_execution_protocol import CodeExecutionFilterSpec
from synthorg.persistence.knowledge_usage_protocol import KnowledgeUsageFilterSpec
from synthorg.tools.code_runner import CodeRunnerTool
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import FakeClock, mock_of
from tests.unit.deliverable_receipts._fakes import (
    InMemoryCodeExecutionRecordRepository,
    InMemoryKnowledgeUsageRecordRepository,
)

if TYPE_CHECKING:
    from synthorg.tools.sandbox.protocol import SandboxBackend

pytestmark = pytest.mark.unit

_HASH = "a" * 64
_IDENTITY = ExecutionIdentity(
    execution_id="exec-1",
    task_id="task-1",
    project_id="proj-1",
)


def _hit(*, source_id: str = "src-1", chunk_id: str = "chunk-1") -> KnowledgeHit:
    return KnowledgeHit(
        chunk_text="some text",
        relevance_score=0.9,
        citation=Citation(
            source_id=source_id,
            chunk_id=chunk_id,
            source_type=SourceType.REPO,
            title="A source",
            uri="repo://file.py",
            locator=CodeLocator(path="file.py", line_start=1, line_end=5),
            content_hash=_HASH,
        ),
    )


def _knowledge_service(
    *,
    usage_records: InMemoryKnowledgeUsageRecordRepository | None,
    hits: tuple[KnowledgeHit, ...],
) -> KnowledgeService:
    from synthorg.knowledge.config import KnowledgeConfig
    from synthorg.knowledge.indexer import KnowledgeIndexer
    from synthorg.knowledge.retrieval import KnowledgeRetriever
    from synthorg.persistence.knowledge_protocol import KnowledgeSourceRepository

    retriever = mock_of[KnowledgeRetriever]()
    retriever.search = AsyncMock(return_value=hits)
    return KnowledgeService(
        sources=mock_of[KnowledgeSourceRepository](),
        indexer=mock_of[KnowledgeIndexer](),
        retriever=retriever,
        config=mock_of[KnowledgeConfig](),
        usage_records=usage_records,
        clock=FakeClock(),
    )


class TestKnowledgeUsageCapture:
    async def test_records_each_hit_within_scope(self) -> None:
        usage = InMemoryKnowledgeUsageRecordRepository()
        service = _knowledge_service(
            usage_records=usage,
            hits=(_hit(source_id="a"), _hit(source_id="b")),
        )
        with execution_identity_scope(_IDENTITY):
            result = await service.search(query="q", project_id="proj-1")
        assert len(result) == 2
        rows = await usage.query(KnowledgeUsageFilterSpec(execution_id="exec-1"))
        assert {r.source_id for r in rows} == {"a", "b"}
        assert all(r.task_id == "task-1" for r in rows)

    async def test_skips_without_scope(self) -> None:
        usage = InMemoryKnowledgeUsageRecordRepository()
        service = _knowledge_service(usage_records=usage, hits=(_hit(),))
        result = await service.search(query="q", project_id="proj-1")
        assert len(result) == 1
        rows = await usage.query(KnowledgeUsageFilterSpec())
        assert rows == ()

    async def test_no_repo_is_a_noop(self) -> None:
        service = _knowledge_service(usage_records=None, hits=(_hit(),))
        with execution_identity_scope(_IDENTITY):
            result = await service.search(query="q", project_id="proj-1")
        assert len(result) == 1


def _sandbox(*, returncode: int = 0, timed_out: bool = False) -> SandboxBackend:
    from synthorg.tools.sandbox.protocol import SandboxBackend

    backend = mock_of[SandboxBackend]()
    backend.execute = AsyncMock(
        return_value=SandboxResult(
            stdout="5 passed",
            stderr="",
            returncode=returncode,
            timed_out=timed_out,
        ),
    )
    return cast("SandboxBackend", backend)


class TestCodeExecutionCapture:
    async def test_records_test_run_within_scope(self) -> None:
        records = InMemoryCodeExecutionRecordRepository()
        clock = FakeClock()
        tool = CodeRunnerTool(
            sandbox=_sandbox(),
            code_execution_records=records,
            clock=clock,
        )
        with execution_identity_scope(_IDENTITY):
            await tool.execute(
                arguments={
                    "code": "pytest",
                    "language": "bash",
                    "purpose": "tests",
                },
            )
        rows = await records.query(CodeExecutionFilterSpec(execution_id="exec-1"))
        assert len(rows) == 1
        assert rows[0].passed is True
        assert rows[0].returncode == 0
        # ``executed_at`` comes from the injected Clock seam, not wall-clock.
        assert rows[0].executed_at == clock.now()

    async def test_general_run_not_recorded(self) -> None:
        records = InMemoryCodeExecutionRecordRepository()
        tool = CodeRunnerTool(sandbox=_sandbox(), code_execution_records=records)
        with execution_identity_scope(_IDENTITY):
            await tool.execute(
                arguments={"code": "print(1)", "language": "python"},
            )
        rows = await records.query(CodeExecutionFilterSpec())
        assert rows == ()

    async def test_test_run_skipped_without_scope(self) -> None:
        records = InMemoryCodeExecutionRecordRepository()
        tool = CodeRunnerTool(sandbox=_sandbox(), code_execution_records=records)
        await tool.execute(
            arguments={"code": "pytest", "language": "bash", "purpose": "tests"},
        )
        rows = await records.query(CodeExecutionFilterSpec())
        assert rows == ()

    async def test_failed_test_run_recorded_as_failed(self) -> None:
        records = InMemoryCodeExecutionRecordRepository()
        tool = CodeRunnerTool(
            sandbox=_sandbox(returncode=1),
            code_execution_records=records,
        )
        with execution_identity_scope(_IDENTITY):
            await tool.execute(
                arguments={"code": "pytest", "language": "bash", "purpose": "tests"},
            )
        rows = await records.query(CodeExecutionFilterSpec(execution_id="exec-1"))
        assert len(rows) == 1
        assert rows[0].passed is False
        assert rows[0].returncode == 1
