"""In-memory (session-scoped) memory backend.

Dict-based backend implementing ``MemoryBackend`` and
``MemoryCapabilities`` protocols.  No persistence -- all data is
lost when the process exits.  Designed for thread-scoped working
memory (``scratch``, ``working`` namespaces) in the composite
backend.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Final

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.errors import (
    MemoryConnectionError,
    MemoryStoreError,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_CONNECTED,
    MEMORY_BACKEND_CONNECTING,
    MEMORY_BACKEND_DISCONNECTED,
    MEMORY_BACKEND_DISCONNECTING,
    MEMORY_BACKEND_HEALTH_CHECK,
    MEMORY_BACKEND_NOT_CONNECTED,
    MEMORY_ENTRY_COUNTED,
    MEMORY_ENTRY_DELETED,
    MEMORY_ENTRY_FETCHED,
    MEMORY_ENTRY_RETRIEVED,
    MEMORY_ENTRY_STORE_FAILED,
    MEMORY_ENTRY_STORED,
)

logger = get_logger(__name__)
_DEFAULT_MAX_MEMORIES_PER_AGENT: Final[int] = 10000

_ALL_CATEGORIES: frozenset[MemoryCategory] = frozenset(MemoryCategory)


class InMemoryBackend:
    """Dict-based agent memory backend.

    Implements ``MemoryBackend`` and ``MemoryCapabilities`` protocols.

    Args:
        max_memories_per_agent: Per-agent memory limit.
    """

    def __init__(
        self,
        *,
        max_memories_per_agent: int = _DEFAULT_MAX_MEMORIES_PER_AGENT,
    ) -> None:
        if max_memories_per_agent < 1:
            msg = f"max_memories_per_agent must be >= 1, got {max_memories_per_agent}"
            raise ValueError(msg)
        self._max_memories_per_agent = max_memories_per_agent
        self._store: dict[str, dict[str, MemoryEntry]] = {}
        self._connected = False
        self._connect_lock = asyncio.Lock()
        # Hot-path lock guarding _store reads AND writes.  Every method
        # that touches _store (store / retrieve / get / delete / count)
        # acquires this lock so concurrent traffic cannot observe a
        # half-applied mutation or race a delete against a concurrent
        # store / retrieve. Separate from _connect_lock so connect /
        # disconnect do not serialise hot-path traffic.
        self._store_lock = asyncio.Lock()

    # -- Lifecycle ----------------------------------------------------

    async def connect(self) -> None:
        """Mark the backend as connected (idempotent)."""
        if self._connected:
            return
        async with self._connect_lock:
            if self._connected:
                return  # type: ignore[unreachable]
            logger.info(MEMORY_BACKEND_CONNECTING, backend="inmemory")
            self._connected = True
            logger.info(MEMORY_BACKEND_CONNECTED, backend="inmemory")

    async def disconnect(self) -> None:
        """Mark the backend as disconnected (idempotent)."""
        async with self._connect_lock:
            if not self._connected:
                return
            logger.info(MEMORY_BACKEND_DISCONNECTING, backend="inmemory")
            self._connected = False
            logger.info(MEMORY_BACKEND_DISCONNECTED, backend="inmemory")

    async def health_check(self) -> bool:
        """Return connection status."""
        logger.debug(
            MEMORY_BACKEND_HEALTH_CHECK,
            backend="inmemory",
            healthy=self._connected,
        )
        return self._connected

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection."""
        return self._connected

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        return NotBlankStr("inmemory")

    # -- Capabilities -------------------------------------------------

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]:
        """All memory categories are supported."""
        return _ALL_CATEGORIES

    @property
    def supports_graph(self) -> bool:
        """No graph-based memory."""
        return False

    @property
    def supports_temporal(self) -> bool:
        """Temporal tracking via ``created_at``."""
        return True

    @property
    def supports_vector_search(self) -> bool:
        """No embedding model -- substring matching only."""
        return False

    @property
    def supports_shared_access(self) -> bool:
        """No cross-agent shared memory."""
        return False

    @property
    def max_memories_per_agent(self) -> int:
        """Configured per-agent memory limit."""
        return self._max_memories_per_agent

    # -- CRUD ---------------------------------------------------------

    def _require_connected(self) -> None:
        """Raise if not connected."""
        if not self._connected:
            msg = "InMemoryBackend is not connected"
            logger.warning(
                MEMORY_BACKEND_NOT_CONNECTED,
                backend="inmemory",
            )
            raise MemoryConnectionError(msg)

    async def store(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Store a memory entry for an agent.

        Args:
            agent_id: Owning agent identifier.
            request: Memory content and metadata.

        Returns:
            The backend-assigned memory ID.

        Raises:
            MemoryConnectionError: If not connected.
            MemoryStoreError: If the per-agent limit is reached.
        """
        self._require_connected()
        memory_id = NotBlankStr(str(uuid.uuid4()))
        now = datetime.now(UTC)
        entry = MemoryEntry(
            id=memory_id,
            agent_id=agent_id,
            namespace=request.namespace,
            category=request.category,
            content=request.content,
            metadata=request.metadata,
            created_at=now,
            expires_at=request.expires_at,
        )
        async with self._store_lock:
            agent_store = self._store.setdefault(str(agent_id), {})
            # Prune expired entries before checking quota.
            _prune_expired(agent_store)
            if len(agent_store) >= self._max_memories_per_agent:
                msg = (
                    f"Agent {agent_id} has reached the memory limit "
                    f"({self._max_memories_per_agent})"
                )
                logger.warning(
                    MEMORY_ENTRY_STORE_FAILED,
                    agent_id=agent_id,
                    reason="limit_reached",
                    error=msg,
                )
                raise MemoryStoreError(msg)
            agent_store[str(memory_id)] = entry
        logger.debug(
            MEMORY_ENTRY_STORED,
            backend="inmemory",
            agent_id=agent_id,
            memory_id=memory_id,
            category=request.category.value,
            namespace=request.namespace,
        )
        return memory_id

    async def retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Retrieve memories matching the query.

        Text search uses case-insensitive substring matching
        (no embedding model available).

        Args:
            agent_id: Owning agent identifier.
            query: Retrieval parameters.

        Returns:
            Matching entries ordered by ``created_at`` descending.

        Raises:
            MemoryConnectionError: If not connected.
        """
        self._require_connected()
        now = datetime.now(UTC)
        async with self._store_lock:
            agent_store = self._store.get(str(agent_id), {})
            matches = [e for e in agent_store.values() if _matches(e, query, now)]
            matches.sort(key=lambda e: e.created_at, reverse=True)
            result = tuple(matches[: query.limit])
        logger.debug(
            MEMORY_ENTRY_RETRIEVED,
            backend="inmemory",
            agent_id=agent_id,
            count=len(result),
        )
        return result

    async def get(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> MemoryEntry | None:
        """Get a specific memory entry by ID.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Memory identifier.

        Returns:
            The entry, or ``None`` if not found.

        Raises:
            MemoryConnectionError: If not connected.
        """
        self._require_connected()
        async with self._store_lock:
            entry = self._store.get(str(agent_id), {}).get(
                str(memory_id),
            )
        if entry is not None:
            if _is_expired(entry, datetime.now(UTC)):
                return None
            logger.debug(
                MEMORY_ENTRY_FETCHED,
                backend="inmemory",
                agent_id=agent_id,
                memory_id=memory_id,
            )
        return entry

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Delete a specific memory entry.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Memory identifier.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            MemoryConnectionError: If not connected.
        """
        self._require_connected()
        async with self._store_lock:
            agent_store = self._store.get(str(agent_id), {})
            entry = agent_store.pop(str(memory_id), None)
        if entry is not None:
            logger.debug(
                MEMORY_ENTRY_DELETED,
                backend="inmemory",
                agent_id=agent_id,
                memory_id=memory_id,
            )
            return True
        return False

    async def count(
        self,
        agent_id: NotBlankStr,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        """Count memory entries for an agent.

        Args:
            agent_id: Owning agent identifier.
            category: Optional category filter.

        Returns:
            Number of matching entries.

        Raises:
            MemoryConnectionError: If not connected.
        """
        self._require_connected()
        now = datetime.now(UTC)
        async with self._store_lock:
            agent_store = self._store.get(str(agent_id), {})
            if category is None:
                total = sum(1 for e in agent_store.values() if not _is_expired(e, now))
            else:
                total = sum(
                    1
                    for e in agent_store.values()
                    if e.category == category and not _is_expired(e, now)
                )
        logger.debug(
            MEMORY_ENTRY_COUNTED,
            backend="inmemory",
            agent_id=agent_id,
            count=total,
            category=category.value if category else None,
        )
        return total

    # -- Extra (not in protocol) --------------------------------------

    def clear(self, agent_id: NotBlankStr) -> int:
        """Remove all memories for an agent (session cleanup).

        Args:
            agent_id: Agent whose memories to clear.

        Returns:
            Number of entries removed.
        """
        agent_store = self._store.pop(str(agent_id), {})
        return len(agent_store)


# -- Filter helpers (module-private) ----------------------------------


def _prune_expired(store: dict[str, MemoryEntry]) -> None:
    """Remove expired entries from an agent store in-place."""
    now = datetime.now(UTC)
    expired = [mid for mid, entry in store.items() if _is_expired(entry, now)]
    for mid in expired:
        del store[mid]


def _is_expired(entry: MemoryEntry, now: datetime) -> bool:
    """Return True if *entry* has expired."""
    return entry.expires_at is not None and entry.expires_at <= now


def _matches_metadata(entry: MemoryEntry, query: MemoryQuery) -> bool:
    """Check namespace, category, tag, and text filters."""
    if query.namespaces and entry.namespace not in query.namespaces:
        return False
    if query.categories and entry.category not in query.categories:
        return False
    if query.tags and not all(tag in entry.metadata.tags for tag in query.tags):
        return False
    return not (query.text and query.text.lower() not in entry.content.lower())


def _matches(
    entry: MemoryEntry,
    query: MemoryQuery,
    now: datetime,
) -> bool:
    """Return True if *entry* passes all query filters."""
    if _is_expired(entry, now):
        return False
    if not _matches_metadata(entry, query):
        return False
    if query.since is not None and entry.created_at < query.since:
        return False
    return not (query.until is not None and entry.created_at >= query.until)
