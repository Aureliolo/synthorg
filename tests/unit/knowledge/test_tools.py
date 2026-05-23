"""Unit tests for the knowledge agent tools + tool factory.

Validates the untrusted-content chunk wrapping and citation rendering on
``search_knowledge``, the ingest tool's status output, the action-type
classifications, and the per-task factory binding.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.enums import ActionType, SourceType
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.indexer import KnowledgeIndexer
from synthorg.knowledge.retrieval import KnowledgeRetriever
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.tool_factory import KnowledgeToolFactory
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.tools.knowledge.ingest_knowledge import IngestKnowledgeTool
from synthorg.tools.knowledge.search_knowledge import SearchKnowledgeTool
from tests._shared import FakeClock
from tests.unit.knowledge._fakes import (
    FakeChunkProvenanceRepository,
    FakeKnowledgeSourceRepository,
)

pytestmark = pytest.mark.unit


async def _factory() -> KnowledgeToolFactory:
    backend = InMemoryBackend()
    await backend.connect()
    sources = FakeKnowledgeSourceRepository()
    provenance = FakeChunkProvenanceRepository()
    clock = FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC))
    service = KnowledgeService(
        sources=sources,
        indexer=KnowledgeIndexer(backend=backend, provenance=provenance, clock=clock),
        retriever=KnowledgeRetriever(
            backend=backend, sources=sources, provenance=provenance
        ),
        config=KnowledgeConfig(),
        clock=clock,
    )
    return KnowledgeToolFactory(service=service)


class TestKnowledgeTools:
    async def test_search_wraps_chunk_and_renders_citation(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "auth.py").write_text(
            "def f():\n    return checkout_secret()\n", encoding="utf-8"
        )
        factory = await _factory()
        tools = factory.build_tools(project_id=NotBlankStr("proj-1"))
        ingest_tool = next(t for t in tools if isinstance(t, IngestKnowledgeTool))
        search_tool = next(t for t in tools if isinstance(t, SearchKnowledgeTool))

        await ingest_tool.execute(
            arguments={
                "source_type": SourceType.REPO.value,
                "uri": str(tmp_path),
                "title": "Repo",
            }
        )
        result = await search_tool.execute(arguments={"query": "checkout_secret"})
        assert result.is_error is False
        assert "<memory-entry>" in result.content
        assert "checkout_secret" in result.content
        assert "auth.py" in result.content
        assert result.metadata["hit_count"] >= 1
        citations = result.metadata["citations"]
        assert citations
        assert citations[0]["locator_kind"] == "code"

    async def test_ingest_reports_status(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 'checkout'\n", encoding="utf-8")
        factory = await _factory()
        ingest_tool = next(
            t
            for t in factory.build_tools(project_id=NotBlankStr("proj-1"))
            if isinstance(t, IngestKnowledgeTool)
        )
        result = await ingest_tool.execute(
            arguments={
                "source_type": SourceType.REPO.value,
                "uri": str(tmp_path),
                "title": "Repo",
            }
        )
        assert result.is_error is False
        assert result.metadata["status"] == "indexed"
        assert result.metadata["chunk_count"] >= 1

    async def test_action_types(self) -> None:
        factory = await _factory()
        tools = factory.build_tools(project_id=NotBlankStr("proj-1"))
        by_type = {type(t).__name__: t.action_type for t in tools}
        assert by_type["SearchKnowledgeTool"] == ActionType.MEMORY_READ.value
        assert by_type["IngestKnowledgeTool"] == ActionType.KNOWLEDGE_INGEST.value

    async def test_search_invalid_args_is_error(self) -> None:
        factory = await _factory()
        search_tool = next(
            t
            for t in factory.build_tools(project_id=NotBlankStr("proj-1"))
            if isinstance(t, SearchKnowledgeTool)
        )
        result = await search_tool.execute(arguments={"query": ""})
        assert result.is_error is True
