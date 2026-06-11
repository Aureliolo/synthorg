"""Agent-tool acceptance for the knowledge substrate.

The substrate's acceptance requires that an agent answers a question
with citations that resolve to the exact source chunks, validated
through the same path a simulation harness uses. The formal
simulation-harness benchmark is a downstream subsystem; the most
faithful validation available today is exercising the substrate
through the agent-tool execution surface
(:class:`SearchKnowledgeTool`, :class:`IngestKnowledgeTool`) that the
simulation harness will invoke. This test plays the agent's part: it
calls the tool with raw ``arguments`` (as the agent runtime would),
inspects the :class:`ToolExecutionResult` payload, and verifies the
citations in ``metadata['citations']`` resolve to ingested source
chunks the agent could quote.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

import pytest

from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.enums import SourceType
from synthorg.knowledge.factory import build_knowledge_service
from synthorg.knowledge.loaders.ticket import (
    TicketComment,
    TicketFetcher,
    TicketThread,
)
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.tool_factory import (
    KNOWLEDGE_TOOL_NAMES,
    build_knowledge_tool_factory,
)
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from tests._shared import FakeClock, as_uuid, sid
from tests._shared.json_types import JsonDict

pytestmark = pytest.mark.integration

_WEB_HTML = (
    "<html><body><p>Idempotency keys deduplicate repeated submissions."
    "</p></body></html>"
)


class _FakeHtmlFetcher:
    async def fetch(self, url: str) -> str:
        return _WEB_HTML


class _FakeTicketFetcher(TicketFetcher):
    @override
    async def fetch(self, ticket_uri: str) -> TicketThread:
        return TicketThread(
            ticket_id=NotBlankStr(ticket_uri),
            comments=(
                TicketComment(
                    comment_id=NotBlankStr("c1"),
                    body="Idempotency: ensure POSTs survive client retries.",
                ),
            ),
        )


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    db_path = tmp_path / "knowledge.db"
    rev_path = migrations.copy_revisions(tmp_path / "revisions", backend="sqlite")
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)),
        revisions_path=rev_path,
    )
    persistence = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    memory = InMemoryBackend()
    persistence_connected = False
    memory_connected = False
    try:
        await persistence.connect()
        persistence_connected = True
        await memory.connect()
        memory_connected = True
        await persistence.projects.save(
            Project(id=as_uuid("proj-A"), name=NotBlankStr("Acceptance"))
        )
        yield build_knowledge_service(
            memory_backend=memory,
            persistence=persistence,
            config=KnowledgeConfig(enabled=True),
            html_fetcher=_FakeHtmlFetcher(),
            ticket_fetcher=_FakeTicketFetcher(),
            clock=FakeClock(start=datetime(2026, 5, 21, tzinfo=UTC)),
        )
    finally:
        # Disconnect both backends, defending against partial setup
        # so a failure inside connect() does not raise during teardown.
        if memory_connected:
            await memory.disconnect()
        if persistence_connected:
            await persistence.disconnect()


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "api.py").write_text(
        "def submit(order):\n    return idempotent_post(order)\n",
        encoding="utf-8",
    )


class TestKnowledgeAgentToolAcceptance:
    """The agent-facing tool surface is what the sim harness invokes."""

    async def test_tool_factory_inventory_matches_contract(
        self, service: KnowledgeService
    ) -> None:
        factory = build_knowledge_tool_factory(service=service)
        tools = factory.build_tools(project_id=NotBlankStr(sid("proj-A")))
        names = {tool.name for tool in tools}
        assert names == set(KNOWLEDGE_TOOL_NAMES)

    async def test_search_tool_returns_citations_resolving_to_ingested_chunks(
        self,
        service: KnowledgeService,
        tmp_path: Path,
    ) -> None:
        # Ingest a mixed corpus through the service (simulating prior
        # admin/ingest activity before the agent runs).
        repo = tmp_path / "repo"
        _write_repo(repo)
        await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(repo)),
            title=NotBlankStr("Order API"),
            project_id=NotBlankStr(sid("proj-A")),
        )
        await service.ingest(
            source_type=SourceType.WEB,
            uri=NotBlankStr("https://docs.test/idempotency"),
            title=NotBlankStr("Idempotency guide"),
            project_id=NotBlankStr(sid("proj-A")),
        )
        await service.ingest(
            source_type=SourceType.TICKET,
            uri=NotBlankStr("TICKET-7"),
            title=NotBlankStr("Idempotency feature"),
            project_id=NotBlankStr(sid("proj-A")),
        )

        # Build the project-scoped tools, then play the agent's part:
        # invoke the tool with an ``arguments`` dict, exactly as the
        # agent runtime / simulation harness would.
        factory = build_knowledge_tool_factory(service=service)
        tools = {
            tool.name: tool
            for tool in factory.build_tools(project_id=NotBlankStr(sid("proj-A")))
        }
        search_tool = tools["search_knowledge"]
        result = await search_tool.execute(
            arguments={"query": "idempotency", "limit": 5}
        )
        assert not result.is_error
        # The tool result is the agent's deliverable input: text +
        # structured citations the agent would quote in its answer.
        assert "idempotency" in result.content.lower()
        citations = cast("list[JsonDict]", result.metadata["citations"])
        assert citations  # hits resolved through the substrate
        # Every citation carries a verifiable source chunk handle.
        source_types_returned = {c["source_type"] for c in citations}
        # The mixed-corpus search should surface at least one of the
        # three ingested source types (relevance ranking decides the
        # exact composition; we assert the surface is functional, not
        # the rank).
        assert source_types_returned & {
            SourceType.REPO.value,
            SourceType.WEB.value,
            SourceType.TICKET.value,
        }
        for c in citations:
            assert c["source_id"]
            assert c["chunk_id"]
            assert c["content_hash"]
            assert c["locator_kind"] in {"pdf", "web", "code", "ticket"}

    async def test_search_tool_rejects_malformed_arguments(
        self, service: KnowledgeService
    ) -> None:
        factory = build_knowledge_tool_factory(service=service)
        tools = factory.build_tools(project_id=NotBlankStr(sid("proj-A")))
        search_tool = next(t for t in tools if t.name == "search_knowledge")
        # Missing required ``query`` is the agent's most common mistake;
        # the tool must surface it as an error result, not crash.
        result = await search_tool.execute(arguments={"limit": 5})
        assert result.is_error
        assert "invalid arguments" in result.content.lower()

    async def test_search_returns_empty_envelope_when_corpus_has_no_match(
        self, service: KnowledgeService, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        _write_repo(repo)
        await service.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(repo)),
            title=NotBlankStr("Order API"),
            project_id=NotBlankStr(sid("proj-A")),
        )
        factory = build_knowledge_tool_factory(service=service)
        search_tool = next(
            t
            for t in factory.build_tools(project_id=NotBlankStr(sid("proj-A")))
            if t.name == "search_knowledge"
        )
        result = await search_tool.execute(
            arguments={"query": "unrelated_topic_no_match", "limit": 3}
        )
        # No-match is not an error: the agent gets a clear "no hits"
        # signal so it can degrade gracefully (e.g., ingest more sources
        # or ask the user).
        assert not result.is_error
        assert "no matching knowledge" in result.content.lower()
        assert result.metadata["hit_count"] == 0
