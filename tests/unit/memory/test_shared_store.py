"""Org memory adapted as the cross-agent shared knowledge store."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.models import MemoryMetadata, MemoryQuery, MemoryStoreRequest
from synthorg.memory.org.models import OrgFact, OrgFactAuthor
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.memory.shared_store import ORG_NAMESPACE, OrgSharedKnowledgeStore
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _fact(
    content: str,
    *,
    author: str | None,
    category: OrgFactCategory = OrgFactCategory.CONVENTION,
) -> OrgFact:
    return OrgFact(
        content=NotBlankStr(content),
        category=category,
        tags=(NotBlankStr("release"),),
        author=OrgFactAuthor(
            agent_id=NotBlankStr(author) if author is not None else None,
            role=NotBlankStr("engineer") if author is not None else None,
            is_human=author is None,
        ),
        created_at=_CREATED_AT,
    )


def _store(*facts: OrgFact) -> tuple[OrgSharedKnowledgeStore, OrgMemoryBackend]:
    backend: OrgMemoryBackend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=facts),
        write=AsyncMock(return_value=NotBlankStr("fact-001")),
    )
    return OrgSharedKnowledgeStore(backend), backend


class TestOrgSharedKnowledgeStore:
    def test_satisfies_the_shared_knowledge_protocol(self) -> None:
        """The retriever fuses through this protocol, not a concrete type."""
        store, _ = _store()
        assert isinstance(store, SharedKnowledgeStore)

    async def test_facts_arrive_as_org_namespaced_entries(self) -> None:
        """Namespace keeps org knowledge distinguishable after fusion."""
        store, _ = _store(_fact("Reviews need two approvals.", author="agent-a"))

        entries = await store.search_shared(MemoryQuery())

        assert len(entries) == 1
        assert entries[0].namespace == ORG_NAMESPACE
        assert entries[0].agent_id == "agent-a"
        assert entries[0].content == "Reviews need two approvals."

    async def test_procedures_map_to_procedural_and_the_rest_to_semantic(
        self,
    ) -> None:
        """Category drives topic scoping, so the mapping is load-bearing."""
        store, _ = _store(
            _fact("Cut a release tag.", author="a", category=OrgFactCategory.PROCEDURE),
            _fact("The company runs on UTC.", author="b"),
        )

        entries = await store.search_shared(MemoryQuery())

        assert entries[0].category is MemoryCategory.PROCEDURAL
        assert entries[1].category is MemoryCategory.SEMANTIC

    async def test_excluded_author_is_omitted(self) -> None:
        """An agent's own writes already arrive through personal recall."""
        store, _ = _store(
            _fact("Mine.", author="agent-a"),
            _fact("Theirs.", author="agent-b"),
        )

        entries = await store.search_shared(
            MemoryQuery(),
            exclude_agent=NotBlankStr("agent-a"),
        )

        assert [entry.content for entry in entries] == ["Theirs."]

    async def test_human_authored_facts_survive_exclusion(self) -> None:
        """A human-authored policy belongs to no agent, so it always shows."""
        store, _ = _store(_fact("Never log customer data.", author=None))

        entries = await store.search_shared(
            MemoryQuery(),
            exclude_agent=NotBlankStr("agent-a"),
        )

        assert len(entries) == 1

    async def test_publish_records_the_agent_as_author(self) -> None:
        """Provenance is what makes exclusion and audit possible."""
        store, backend = _store()

        fact_id = await store.publish(
            NotBlankStr("agent-a"),
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content=NotBlankStr("Deploys freeze on Fridays."),
                metadata=MemoryMetadata(tags=(NotBlankStr("release"),)),
            ),
        )

        assert fact_id == "fact-001"
        author = backend.write.call_args[1]["author"]  # type: ignore[attr-defined]
        assert author.agent_id == "agent-a"
        request = backend.write.call_args[0][0]  # type: ignore[attr-defined]
        assert request.tags == (NotBlankStr("release"),)

    async def test_retract_fails_loud_rather_than_reporting_a_false_delete(
        self,
    ) -> None:
        """Org memory is append-only; a silent False would read as absent."""
        store, _ = _store()

        with pytest.raises(FeatureNotImplementedError, match="append-only"):
            await store.retract(NotBlankStr("agent-a"), NotBlankStr("fact-001"))
