"""Conformance tests for ``MemoryVectorRepository``.

Runs durable agent memory against every backend behind the shared
``backend`` fixture, so SQLite (sqlite-vec + inverted index) and Postgres
(pgvector + inverted index) are held to one behavioural contract rather
than two implementations that merely look similar.

Dense retrieval is deliberately absent here. It depends on a runtime
extension (``sqlite-vec``) or a Postgres extension (``pgvector``) that a
bare test image need not carry, and it is covered against a real index in
the per-backend suites. What must hold on both backends regardless is the
contract everything else depends on: durable round-tripping, agent
scoping, filter semantics, lexical ranking and lifecycle.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.vector_spec import MemoryVectorSearchSpec
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_AGENT = NotBlankStr("agent-1")


def _entry(  # noqa: PLR0913 -- a fixture builder; each field is one test axis
    memory_id: str,
    content: str,
    *,
    agent_id: str = "agent-1",
    category: MemoryCategory = MemoryCategory.SEMANTIC,
    namespace: str = "default",
    tags: tuple[str, ...] = (),
    created_at: datetime = _NOW,
    expires_at: datetime | None = None,
) -> MemoryEntry:
    """Build a memory entry for the conformance suite."""
    return MemoryEntry(
        id=NotBlankStr(memory_id),
        agent_id=NotBlankStr(agent_id),
        namespace=NotBlankStr(namespace),
        category=category,
        content=NotBlankStr(content),
        metadata=MemoryMetadata(tags=tuple(NotBlankStr(t) for t in tags)),
        created_at=created_at,
        expires_at=expires_at,
    )


class TestRoundTrip:
    """Durability and marshalling across both engines."""

    async def test_stored_entry_round_trips(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(
            _entry("m1", "rollback procedure", tags=("runbook",)),
            embedding=None,
        )

        fetched = await repo.get(_AGENT, NotBlankStr("m1"))

        assert fetched is not None
        assert fetched.content == "rollback procedure"
        assert fetched.category is MemoryCategory.SEMANTIC
        assert fetched.metadata.tags == ("runbook",)
        assert fetched.created_at == _NOW

    async def test_upsert_replaces_existing_entry(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "first"), embedding=None)
        await repo.upsert(_entry("m1", "second"), embedding=None)

        fetched = await repo.get(_AGENT, NotBlankStr("m1"))

        assert fetched is not None
        assert fetched.content == "second"

    async def test_expiry_round_trips(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        expires = _NOW + timedelta(hours=1)
        await repo.upsert(_entry("m1", "temporary", expires_at=expires), embedding=None)

        fetched = await repo.get(_AGENT, NotBlankStr("m1"))

        assert fetched is not None
        assert fetched.expires_at == expires


class TestOwnershipScoping:
    """One agent must never reach another agent's memories."""

    async def test_get_is_agent_scoped(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "private"), embedding=None)

        assert await repo.get(NotBlankStr("agent-2"), NotBlankStr("m1")) is None

    async def test_delete_is_agent_scoped(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "private"), embedding=None)

        assert await repo.delete(NotBlankStr("agent-2"), NotBlankStr("m1")) is False
        assert await repo.get(_AGENT, NotBlankStr("m1")) is not None

    async def test_listing_is_agent_scoped(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "mine"), embedding=None)
        await repo.upsert(_entry("m2", "theirs", agent_id="agent-2"), embedding=None)

        hits = await repo.list_filtered(MemoryVectorSearchSpec(agent_id=_AGENT))

        assert [h.id for h in hits] == ["m1"]

    async def test_count_is_agent_scoped(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "mine"), embedding=None)
        await repo.upsert(_entry("m2", "theirs", agent_id="agent-2"), embedding=None)

        assert await repo.count(_AGENT) == 1


class TestFilters:
    """Filter semantics must be identical on both engines."""

    async def test_category_filter(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(
            _entry("m1", "alpha", category=MemoryCategory.SEMANTIC), embedding=None
        )
        await repo.upsert(
            _entry("m2", "beta", category=MemoryCategory.PROCEDURAL), embedding=None
        )

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(
                agent_id=_AGENT,
                categories=frozenset({MemoryCategory.PROCEDURAL}),
            )
        )

        assert [h.id for h in hits] == ["m2"]

    async def test_namespace_filter(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "alpha", namespace="scratch"), embedding=None)
        await repo.upsert(_entry("m2", "beta"), embedding=None)

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(
                agent_id=_AGENT,
                namespaces=frozenset({NotBlankStr("scratch")}),
            )
        )

        assert [h.id for h in hits] == ["m1"]

    async def test_tags_use_and_semantics(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "alpha", tags=("x", "y")), embedding=None)
        await repo.upsert(_entry("m2", "beta", tags=("x",)), embedding=None)

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(
                agent_id=_AGENT,
                tags=(NotBlankStr("x"), NotBlankStr("y")),
            )
        )

        assert [h.id for h in hits] == ["m1"]

    async def test_expired_entries_excluded(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(
            _entry("m1", "alpha", expires_at=_NOW + timedelta(hours=1)),
            embedding=None,
        )

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(agent_id=_AGENT, now=_NOW + timedelta(hours=2))
        )

        assert hits == ()

    async def test_created_before_filter(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(
            _entry("old", "alpha", created_at=_NOW - timedelta(days=2)), embedding=None
        )
        await repo.upsert(_entry("new", "beta"), embedding=None)

        hits = await repo.list_filtered(
            MemoryVectorSearchSpec(agent_id=_AGENT, until=_NOW - timedelta(days=1))
        )

        assert [h.id for h in hits] == ["old"]


class TestLexicalRanking:
    """BM25 is shared code, so both engines must rank identically."""

    async def test_matching_term_is_found(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "rollback the deployment"), embedding=None)
        await repo.upsert(_entry("m2", "unrelated content"), embedding=None)

        hits = await repo.search_lexical(
            MemoryVectorSearchSpec(agent_id=_AGENT, text=NotBlankStr("rollback"))
        )

        assert [h.id for h in hits] == ["m1"]

    async def test_more_query_terms_ranks_higher(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "rollback deployment procedure"), embedding=None)
        await repo.upsert(_entry("m2", "rollback only"), embedding=None)

        hits = await repo.search_lexical(
            MemoryVectorSearchSpec(
                agent_id=_AGENT, text=NotBlankStr("rollback deployment")
            )
        )

        assert hits[0].id == "m1"

    async def test_no_match_returns_empty(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "rollback"), embedding=None)

        hits = await repo.search_lexical(
            MemoryVectorSearchSpec(agent_id=_AGENT, text=NotBlankStr("kubernetes"))
        )

        assert hits == ()

    async def test_reindex_on_update_drops_stale_terms(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "rollback procedure"), embedding=None)
        await repo.upsert(_entry("m1", "kubernetes scaling"), embedding=None)

        stale = await repo.search_lexical(
            MemoryVectorSearchSpec(agent_id=_AGENT, text=NotBlankStr("rollback"))
        )
        fresh = await repo.search_lexical(
            MemoryVectorSearchSpec(agent_id=_AGENT, text=NotBlankStr("kubernetes"))
        )

        assert stale == ()
        assert [h.id for h in fresh] == ["m1"]


class TestLifecycle:
    """Deletion, expiry purging and the per-agent cap helper."""

    async def test_delete_removes_entry(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(_entry("m1", "alpha"), embedding=None)

        assert await repo.delete(_AGENT, NotBlankStr("m1")) is True
        assert await repo.get(_AGENT, NotBlankStr("m1")) is None

    async def test_delete_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.memory_vectors

        assert await repo.delete(_AGENT, NotBlankStr("absent")) is False

    async def test_purge_expired_removes_only_expired(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.memory_vectors
        await repo.upsert(
            _entry("stale", "alpha", expires_at=_NOW + timedelta(hours=1)),
            embedding=None,
        )
        await repo.upsert(_entry("live", "beta"), embedding=None)

        purged = await repo.purge_expired(_NOW + timedelta(hours=2))

        assert purged == 1
        assert await repo.get(_AGENT, NotBlankStr("live")) is not None

    async def test_oldest_ids_returns_oldest_first(
        self, backend: PersistenceBackend
    ) -> None:
        repo = backend.memory_vectors
        await repo.upsert(
            _entry("old", "alpha", created_at=_NOW - timedelta(days=2)), embedding=None
        )
        await repo.upsert(_entry("new", "beta"), embedding=None)

        assert await repo.oldest_ids(_AGENT, excess=1) == ("old",)

    async def test_oldest_ids_non_positive_excess_is_empty(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.memory_vectors.oldest_ids(_AGENT, excess=0) == ()

    async def test_count_by_category(self, backend: PersistenceBackend) -> None:
        repo = backend.memory_vectors
        await repo.upsert(
            _entry("m1", "alpha", category=MemoryCategory.SEMANTIC), embedding=None
        )
        await repo.upsert(
            _entry("m2", "beta", category=MemoryCategory.PROCEDURAL), embedding=None
        )

        assert await repo.count(_AGENT, category=MemoryCategory.PROCEDURAL) == 1
