"""Agent-memory vector repository protocol.

Lives in ``persistence/`` so the durable-state contract sits beside every
other repository protocol and the raw SQL stays inside the persistence
boundary. Domain types stay in :mod:`synthorg.memory.models` and
:mod:`synthorg.memory.vector_spec`.

This protocol is BESPOKE per ADR-0001 D7 rather than composed from the
generic categories because:

1. **Dual ranked retrieval**: :meth:`search_dense` and
   :meth:`search_lexical` return two independently ranked lists for
   Reciprocal Rank Fusion in the memory package. No generic category
   models a ranked-retrieval read.
2. **Embedding is a write-side sidecar**: :meth:`upsert` takes an
   embedding alongside the entity, which
   ``IdKeyedRepository.save(entity)`` cannot express.
3. **Ownership-scoped mutation**: every read and write is scoped by
   ``agent_id`` so one agent can never reach another's memories, a
   domain invariant the generic key-only surface would lose.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry
from synthorg.memory.vector_spec import MemoryVectorSearchSpec


@runtime_checkable
class MemoryVectorRepository(Protocol):
    """Durable agent-memory storage with hybrid dense + lexical retrieval.

    Implementations back the dense index with pgvector (Postgres) or
    sqlite-vec (SQLite), and the lexical index with the same declarative
    ``memory_entry_terms`` inverted index on both, scored by the shared
    BM25 code (neither FTS5 nor tsvector), so the two backends rank
    identically and stay at API parity.

    Every method raises ``PersistenceError`` on failure, except
    :meth:`ensure_ready`, which deliberately degrades rather than raising
    (see its docstring).
    """

    async def ensure_ready(self, dimensions: int | None = None) -> None:
        """Prepare the dense index for a given embedding width.

        Idempotent. Deliberately does not raise when the vector index
        cannot be prepared: this repository shares a connection with
        every other feature, so a missing extension must not take
        persistence down. The outcome is reported through
        :attr:`supports_dense_search`, and the memory backend is what
        turns an absent index into a loud failure at its own boundary.

        Args:
            dimensions: Embedding width. Supplied here rather than at
                construction because persistence builds the repository
                before the embedder is resolved. ``None`` leaves recall
                lexical-only.
        """
        ...

    @property
    def supports_dense_search(self) -> bool:
        """Whether the dense vector index is available on this connection.

        SQLite loads ``sqlite-vec`` as a runtime extension, which can be
        unavailable on a hardened build. Callers consult this before
        relying on :meth:`search_dense` so a missing extension surfaces
        as an explicit capability gap rather than silently empty recall.
        """
        ...

    async def upsert(
        self,
        entry: MemoryEntry,
        /,
        *,
        embedding: tuple[float, ...] | None,
    ) -> None:
        """Insert or replace one memory entry and its embedding.

        Args:
            entry: The memory entry to persist. ``entry.id`` is the
                primary key; re-upserting the same id replaces the row
                and its embedding.
            embedding: Dense vector for the entry, or ``None`` to store
                the row without a dense index entry (lexical-only).

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
        /,
    ) -> MemoryEntry | None:
        """Read one entry by id, scoped to its owning agent.

        Returns ``None`` when the entry is absent or owned by another
        agent; the two cases are deliberately indistinguishable so a
        caller cannot probe for another agent's memory ids.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Memory identifier.

        Returns:
            The entry, or ``None``.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
        /,
    ) -> bool:
        """Delete one entry and its embedding, scoped to its owner.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Memory identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if absent or owned
            by another agent.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def search_dense(
        self,
        spec: MemoryVectorSearchSpec,
        /,
    ) -> tuple[MemoryEntry, ...]:
        """Rank entries by embedding similarity to ``spec.embedding``.

        Returns an empty tuple when ``spec.embedding`` is ``None`` or
        the dense index is unavailable, so callers can always call both
        searches and fuse whatever comes back.

        Args:
            spec: Filters, query embedding and row limit.

        Returns:
            Entries in descending similarity order, ``relevance_score``
            populated with the normalised similarity.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def search_lexical(
        self,
        spec: MemoryVectorSearchSpec,
        /,
    ) -> tuple[MemoryEntry, ...]:
        """Rank entries by full-text relevance to ``spec.text``.

        Returns an empty tuple when ``spec.text`` is ``None``.

        Args:
            spec: Filters, query text and row limit.

        Returns:
            Entries in descending lexical-rank order,
            ``relevance_score`` populated with the normalised rank.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_filtered(  # lint-allow: list-pagination -- spec.limit bounds it
        self,
        spec: MemoryVectorSearchSpec,
        /,
    ) -> tuple[MemoryEntry, ...]:
        """Return filter-matching entries with no relevance ranking.

        Backs metadata-only retrieval (``MemoryQuery.text is None``),
        ordered newest-first so the result is deterministic.

        Args:
            spec: Filters and row limit. ``text`` and ``embedding`` are
                ignored.

        Returns:
            Matching entries, newest first.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count(
        self,
        agent_id: NotBlankStr,
        /,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        """Count an agent's entries, optionally filtered by category.

        Args:
            agent_id: Owning agent identifier.
            category: Optional category filter.

        Returns:
            Number of matching entries.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def purge_expired(self, now: datetime, /) -> int:
        """Delete every entry whose ``expires_at`` is at or before ``now``.

        Args:
            now: Reference instant. Must be timezone-aware; the
                annotation is plain ``datetime`` because Pydantic's
                ``AwareDatetime`` is a field annotation that typeguard
                cannot check on a function parameter.

        Returns:
            Number of rows deleted.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def oldest_ids(
        self,
        agent_id: NotBlankStr,
        /,
        *,
        excess: int,
    ) -> tuple[NotBlankStr, ...]:
        """Return the ``excess`` oldest entry ids for an agent.

        Backs the per-agent cap: the caller deletes these to bring an
        agent back under ``max_memories_per_agent``.

        Args:
            agent_id: Owning agent identifier.
            excess: How many ids to return.

        Returns:
            Entry ids, oldest first. Empty when ``excess`` is not
            positive.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
