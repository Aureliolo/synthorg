# module-kind: adapter
"""SQL-backed agent memory with hybrid dense + lexical retrieval.

One backend serves both persistence engines. Everything above the SQL is
identical (embedding, fusion, the per-agent cap, ownership scoping), so
the only thing that varies is the injected
:class:`MemoryVectorRepository`. Writing two near-identical backends
would have given the two engines two chances to diverge in behaviour
rather than only in SQL.

Retrieval is the two-stage shape the IR literature converges on: recall
wide from two orthogonal signals (dense vectors and BM25 over the
inverted index), then fuse by Reciprocal Rank Fusion. RRF operates on
ranks, so it sidesteps the score-normalisation problem that makes a
weighted sum of cosine distance and BM25 unreliable.
"""

import asyncio
import math
from typing import Final
from uuid import uuid4

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.persistence_errors import PersistenceError
from synthorg.core.types import NotBlankStr
from synthorg.memory.embedder_port import TextEmbedder
from synthorg.memory.errors import (
    MemoryConnectionError,
    MemoryDenseSearchUnavailableError,
    MemoryEmbeddingError,
    MemoryRetrievalError,
    MemoryStoreError,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryQuery,
    MemoryStoreRequest,
    MemoryUpdateRequest,
)
from synthorg.memory.ranking_rrf import fuse_ranked_lists
from synthorg.memory.vector_spec import MAX_SEARCH_LIMIT, MemoryVectorSearchSpec
from synthorg.memory.write_gate import SUPERSEDED_TAG
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_CONNECTED,
    MEMORY_BACKEND_DISCONNECTED,
    MEMORY_BACKEND_NOT_CONNECTED,
    MEMORY_ENTRY_DELETED,
    MEMORY_ENTRY_RETRIEVAL_FAILED,
    MEMORY_ENTRY_STORE_FAILED,
    MEMORY_ENTRY_STORED,
)
from synthorg.persistence.memory_vector_protocol import MemoryVectorRepository

logger = get_logger(__name__)

_BACKEND_NAME: Final[NotBlankStr] = NotBlankStr("sqlvector")
# Over-fetch each arm before fusion so a document ranked mid-list by one
# signal can still reach the fused top-k on the strength of the other.
# Fusing two already-truncated top-k lists throws that away.
_RECALL_MULTIPLIER: Final[int] = 3
# The dense arm over-fetches much wider than the lexical one. Every
# metadata filter (namespace, category, tags, and the always-on
# superseded exclusion) is applied AFTER the k nearest are chosen, so a
# narrow k spends all its slots on rows that a project-scoped or
# category-scoped query then discards, and dense recall silently goes
# empty as the corpus grows. Lexical filters inside the query, so it does
# not need this. Both are still capped at ``MAX_SEARCH_LIMIT``.
_DENSE_RECALL_MULTIPLIER: Final[int] = 20
# Dense KNN always returns its k nearest neighbours, however unrelated
# they are: a nonsense query against a store of one memory still returns
# that memory. Left unchecked that is the wrong-recall failure mode, and
# it cannot be caught downstream because RRF min-max normalises the top
# fused hit to exactly 1.0 regardless of quality.
#
# This floor is the coarse pre-filter for that: every vector is
# L2-normalised by _unit_vector before it is stored or searched with, so
# two orthogonal (entirely unrelated) vectors sit at L2 distance sqrt(2),
# which maps to 1/(1+sqrt(2)) ~= 0.414. Requiring more than 0.5
# therefore means "closer than orthogonal", which is a geometric
# statement rather than a tuned magic number.
#
# It is deliberately NOT the calibrated relevance gate. Raw similarity is
# a poor binary judge of whether a memory will actually help; that
# decision belongs to the reranker stage.
_ORTHOGONAL_SIMILARITY: Final[float] = 0.5


def _unit_vector(embedding: tuple[float, ...]) -> tuple[float, ...]:
    """Scale an embedding to unit length.

    The relevance floor is a geometric statement about angles, and L2
    distance only tracks the angle between two vectors once both have the
    same magnitude. The embedder is operator-configurable and not every
    model emits normalised output, so the invariant is established here
    rather than assumed.

    Returns:
        The unit-length vector, or the input unchanged when it carries no
        magnitude to normalise.
    """
    norm = math.sqrt(math.fsum(component * component for component in embedding))
    if norm == 0.0:
        return embedding
    return tuple(component / norm for component in embedding)


class SqlVectorBackend:
    """Durable agent memory over a SQL vector repository.

    Args:
        repository: The backend-specific vector repository.
        embedder: Text embedder, or ``None`` for lexical-only recall.
        max_memories_per_agent: Cap enforced after each store.
        clock: Time source; tests inject a fake.
    """

    def __init__(
        self,
        repository: MemoryVectorRepository,
        *,
        embedder: TextEmbedder | None = None,
        max_memories_per_agent: int,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._max_memories_per_agent = max_memories_per_agent
        self._clock = clock or SystemClock()
        self._connected = False
        # Per-agent, not global: cap enforcement is a per-agent
        # read-then-write, so one busy agent's eviction must not serialise
        # every other agent's stores. ``setdefault`` is atomic here (no
        # await between check and insert on a single event loop).
        self._cap_locks: dict[NotBlankStr, asyncio.Lock] = {}

    @property
    def is_connected(self) -> bool:
        """Whether the backend is ready for reads and writes."""
        return self._connected

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        return _BACKEND_NAME

    @property
    def supports_dense_search(self) -> bool:
        """Whether semantic recall is available.

        Requires both an embedder to turn text into vectors and a
        repository whose dense index is present.
        """
        return self._embedder is not None and self._repository.supports_dense_search

    async def connect(self) -> None:
        """Prepare the dense index and mark the backend usable.

        The embedder's width is handed to the repository here: the
        backend is the first place that knows both the store and the
        embedder, so it is where the two are reconciled.

        Raises:
            MemoryDenseSearchUnavailableError: If an embedder is wired
                but the store could not prepare its dense index. The
                repository degrades rather than raising so persistence
                stays up for every non-memory feature sharing the
                connection; this is the boundary where that degradation
                becomes a refusal, because lexical-only recall behind an
                operator who configured semantic recall reads as working
                memory while returning the wrong things.
        """
        await self._repository.ensure_ready(
            self._embedder.dimensions if self._embedder is not None else None
        )
        if self._embedder is not None and not self._repository.supports_dense_search:
            msg = (
                "Semantic memory is configured but the dense index is "
                "unavailable; recall would silently degrade to keyword "
                "matching. See the memory.dense_index.* events for the "
                "underlying cause."
            )
            raise MemoryDenseSearchUnavailableError(msg)
        self._connected = True
        logger.info(
            MEMORY_BACKEND_CONNECTED,
            backend=_BACKEND_NAME,
            dense_search=self.supports_dense_search,
        )

    async def disconnect(self) -> None:
        """Mark the backend unusable. The connection itself is owned elsewhere."""
        self._connected = False
        logger.info(MEMORY_BACKEND_DISCONNECTED, backend=_BACKEND_NAME)

    async def health_check(self) -> bool:
        """Probe the store with a cheap read.

        Returns:
            ``True`` when the store answers, ``False`` otherwise.
        """
        if not self._connected:
            return False
        try:
            await self._repository.count(NotBlankStr("health-check"))
        except PersistenceError as exc:
            logger.warning(
                MEMORY_ENTRY_RETRIEVAL_FAILED,
                backend=_BACKEND_NAME,
                operation="health_check",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        return True

    def _require_connected(self, operation: str) -> None:
        """Reject an operation attempted before ``connect()``.

        Raises:
            MemoryConnectionError: If the backend is not connected.
        """
        if self._connected:
            return
        logger.warning(
            MEMORY_BACKEND_NOT_CONNECTED,
            backend=_BACKEND_NAME,
            operation=operation,
        )
        msg = f"{_BACKEND_NAME} backend is not connected; call connect() first"
        raise MemoryConnectionError(msg)

    async def _embed(self, text: str) -> tuple[float, ...] | None:
        """Embed one text, or return ``None`` when no embedder is wired.

        Returns:
            The vector, or ``None`` when recall is lexical-only.

        Raises:
            MemoryEmbeddingError: If the embedder fails.
        """
        if self._embedder is None:
            return None
        vectors = await self._embedder.embed_many((text,))
        if not vectors:
            msg = "Embedder returned no vector for a non-empty text"
            raise MemoryEmbeddingError(msg)
        return _unit_vector(vectors[0])

    async def _query_embedding(self, text: str) -> tuple[float, ...] | None:
        """Embed a query, dropping a vector that carries no signal.

        An all-zero vector means the embedder recognised nothing in the
        query. Searching with it would rank by an arbitrary tie-break and
        return confidently wrong neighbours, so the dense arm is skipped
        and recall falls to the lexical arm alone.

        Returns:
            The query vector, or ``None`` when there is no signal to
            search with.
        """
        embedding = await self._embed(text)
        if embedding is None or not any(embedding):
            return None
        return embedding

    async def store(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Persist one memory and enforce the per-agent cap.

        Returns:
            The assigned memory id.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the write fails.
        """
        self._require_connected("store")
        entry = MemoryEntry(
            id=NotBlankStr(str(uuid4())),
            agent_id=agent_id,
            namespace=request.namespace,
            category=request.category,
            content=request.content,
            metadata=request.metadata,
            created_at=self._clock.now(),
            expires_at=request.expires_at,
        )
        embedding = await self._embed(request.content)
        try:
            await self._repository.upsert(entry, embedding=embedding)
        except PersistenceError as exc:
            logger.warning(
                MEMORY_ENTRY_STORE_FAILED,
                backend=_BACKEND_NAME,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to store memory for agent {agent_id!r}"
            raise MemoryStoreError(msg) from exc
        await self._enforce_cap(agent_id)
        logger.debug(
            MEMORY_ENTRY_STORED,
            backend=_BACKEND_NAME,
            agent_id=agent_id,
            category=request.category.value,
        )
        return entry.id

    async def _enforce_cap(self, agent_id: NotBlankStr) -> None:
        """Delete the oldest entries once an agent exceeds its cap.

        Count, select-oldest and delete are three round trips, so two
        concurrent stores for one agent would each compute ``excess``
        from the same pre-delete count and evict twice as much as either
        needed. Serialising the whole sequence per agent keeps it
        read-then-write consistent without blocking other agents.
        """
        lock = self._cap_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            total = await self._repository.count(agent_id)
            excess = total - self._max_memories_per_agent
            if excess <= 0:
                return
            for memory_id in await self._repository.oldest_ids(agent_id, excess=excess):
                await self._repository.delete(agent_id, memory_id)

    def _spec(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
        *,
        embedding: tuple[float, ...] | None,
        limit: int,
    ) -> MemoryVectorSearchSpec:
        """Translate a domain query into a repository search spec.

        Returns:
            The spec both retrieval arms share, so a filter can never
            apply to one arm and not the other.
        """
        return MemoryVectorSearchSpec(
            agent_id=agent_id,
            text=query.text,
            embedding=embedding,
            namespaces=query.namespaces,
            categories=query.categories,
            tags=query.tags,
            excluded_tags=(
                () if query.include_superseded else (NotBlankStr(SUPERSEDED_TAG),)
            ),
            limit=limit,
            oldest_first=query.oldest_first,
            since=query.since,
            until=query.until,
            now=self._clock.now(),
        )

    async def retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Retrieve memories ranked by hybrid relevance.

        With no ``query.text`` this is metadata-only filtering; with text
        it fuses dense and lexical recall by RRF.

        Returns:
            Matching entries, best first.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the retrieval fails.
        """
        self._require_connected("retrieve")
        try:
            if query.text is None:
                return await self._repository.list_filtered(
                    self._spec(agent_id, query, embedding=None, limit=query.limit)
                )
            return await self._hybrid_retrieve(agent_id, query)
        except PersistenceError as exc:
            logger.warning(
                MEMORY_ENTRY_RETRIEVAL_FAILED,
                backend=_BACKEND_NAME,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to retrieve memories for agent {agent_id!r}"
            raise MemoryRetrievalError(msg) from exc

    async def _hybrid_retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Run both retrieval arms and fuse them.

        Returns:
            Fused entries, best first, filtered by ``min_relevance``.
        """
        # A large max_memories setting multiplied by the over-fetch
        # width can exceed what the spec accepts, and that ValidationError
        # would surface as "memory recalled nothing" rather than as a
        # configuration error. Clamping keeps a legal settings
        # combination retrievable, just with a narrower over-fetch.
        lexical_limit = min(query.limit * _RECALL_MULTIPLIER, MAX_SEARCH_LIMIT)
        dense_limit = min(query.limit * _DENSE_RECALL_MULTIPLIER, MAX_SEARCH_LIMIT)
        embedding = await self._query_embedding(query.text) if query.text else None
        dense_spec = self._spec(agent_id, query, embedding=embedding, limit=dense_limit)
        lexical_spec = self._spec(
            agent_id, query, embedding=embedding, limit=lexical_limit
        )
        dense = self._drop_unrelated(await self._repository.search_dense(dense_spec))
        lexical = await self._repository.search_lexical(lexical_spec)
        arms = tuple(arm for arm in (dense, lexical) if arm)
        if not arms:
            return ()
        fused = fuse_ranked_lists(arms, max_results=query.limit)
        return tuple(
            scored.entry.model_copy(update={"relevance_score": scored.combined_score})
            for scored in fused
            if scored.combined_score >= query.min_relevance
        )

    @staticmethod
    def _drop_unrelated(hits: tuple[MemoryEntry, ...]) -> tuple[MemoryEntry, ...]:
        """Discard dense hits no closer than orthogonal to the query.

        Returns:
            The hits that carry real vector evidence.
        """
        return tuple(
            hit
            for hit in hits
            if hit.relevance_score is not None
            and hit.relevance_score > _ORTHOGONAL_SIMILARITY
        )

    async def get(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> MemoryEntry | None:
        """Read one memory owned by ``agent_id``.

        Returns:
            The entry, or ``None`` when absent or owned by someone else.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the read fails.
        """
        self._require_connected("get")
        try:
            return await self._repository.get(agent_id, memory_id)
        except PersistenceError as exc:
            msg = f"Failed to fetch memory {memory_id!r}"
            raise MemoryRetrievalError(msg) from exc

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Delete one memory owned by ``agent_id``.

        Returns:
            ``True`` when a row was removed.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the delete fails.
        """
        self._require_connected("delete")
        try:
            deleted = await self._repository.delete(agent_id, memory_id)
        except PersistenceError as exc:
            msg = f"Failed to delete memory {memory_id!r}"
            raise MemoryStoreError(msg) from exc
        if deleted:
            logger.debug(
                MEMORY_ENTRY_DELETED,
                backend=_BACKEND_NAME,
                agent_id=agent_id,
            )
        return deleted

    async def update(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
        request: MemoryUpdateRequest,
    ) -> MemoryEntry | None:
        """Apply a partial update, re-embedding when content changes.

        Returns:
            The updated entry, or ``None`` when absent or owned by
            someone else.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the write fails.
        """
        self._require_connected("update")
        existing = await self.get(agent_id, memory_id)
        if existing is None:
            return None
        updates: dict[str, object] = {"updated_at": self._clock.now()}
        if request.content is not None:
            updates["content"] = request.content
        if request.metadata is not None:
            updates["metadata"] = request.metadata
        if request.clear_expiration:
            updates["expires_at"] = None
        elif request.expires_at is not None:
            updates["expires_at"] = request.expires_at
        updated = existing.model_copy(update=updates)
        # Always re-embed the (possibly unchanged) content. The repository
        # treats ``embedding=None`` as "drop the dense row", so passing
        # None on a metadata-only edit (tags, expiry) would silently strip
        # the entry from dense recall while it stays lexically findable,
        # splitting the two RRF arms. Re-embedding the same content
        # reproduces the same vector, so the dense row is preserved.
        embedding = await self._embed(updated.content)
        try:
            await self._repository.upsert(updated, embedding=embedding)
        except PersistenceError as exc:
            msg = f"Failed to update memory {memory_id!r}"
            raise MemoryStoreError(msg) from exc
        return updated

    async def count(
        self,
        agent_id: NotBlankStr,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        """Count an agent's memories.

        Returns:
            The number of matching entries.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the count fails.
        """
        self._require_connected("count")
        try:
            return await self._repository.count(agent_id, category=category)
        except PersistenceError as exc:
            msg = f"Failed to count memories for agent {agent_id!r}"
            raise MemoryRetrievalError(msg) from exc


__all__ = ["SqlVectorBackend"]
