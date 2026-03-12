"""Mem0 memory backend adapter.

Implements ``MemoryBackend``, ``MemoryCapabilities``, and
``SharedKnowledgeStore`` protocols using Mem0 (embedded Qdrant + SQLite)
as the storage layer.

All Mem0 calls are synchronous — they run in ``asyncio.to_thread()``
to avoid blocking the event loop.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from ai_company.core.enums import MemoryCategory
from ai_company.core.types import NotBlankStr
from ai_company.memory.backends.mem0.config import (
    Mem0BackendConfig,
    build_mem0_config_dict,
)
from ai_company.memory.backends.mem0.mappers import (
    apply_post_filters,
    build_mem0_metadata,
    mem0_result_to_entry,
    query_to_mem0_getall_args,
    query_to_mem0_search_args,
)
from ai_company.memory.errors import (
    MemoryConnectionError,
    MemoryRetrievalError,
    MemoryStoreError,
)
from ai_company.observability import get_logger

if TYPE_CHECKING:
    from ai_company.memory.models import (
        MemoryEntry,
        MemoryQuery,
        MemoryStoreRequest,
    )
from ai_company.observability.events.memory import (
    MEMORY_BACKEND_CONNECTED,
    MEMORY_BACKEND_CONNECTING,
    MEMORY_BACKEND_CONNECTION_FAILED,
    MEMORY_BACKEND_CREATED,
    MEMORY_BACKEND_DISCONNECTED,
    MEMORY_BACKEND_DISCONNECTING,
    MEMORY_BACKEND_HEALTH_CHECK,
    MEMORY_BACKEND_NOT_CONNECTED,
    MEMORY_ENTRY_COUNT_FAILED,
    MEMORY_ENTRY_COUNTED,
    MEMORY_ENTRY_DELETE_FAILED,
    MEMORY_ENTRY_DELETED,
    MEMORY_ENTRY_FETCH_FAILED,
    MEMORY_ENTRY_FETCHED,
    MEMORY_ENTRY_RETRIEVAL_FAILED,
    MEMORY_ENTRY_RETRIEVED,
    MEMORY_ENTRY_STORE_FAILED,
    MEMORY_ENTRY_STORED,
    MEMORY_SHARED_PUBLISH_FAILED,
    MEMORY_SHARED_PUBLISHED,
    MEMORY_SHARED_RETRACT_FAILED,
    MEMORY_SHARED_RETRACTED,
    MEMORY_SHARED_SEARCH_FAILED,
    MEMORY_SHARED_SEARCHED,
)

logger = get_logger(__name__)

# Reserved user_id for the shared knowledge namespace.
_SHARED_NAMESPACE: str = "__synthorg_shared__"

# Metadata key to track who published a shared memory.
_PUBLISHER_KEY: str = "_synthorg_publisher"


class Mem0MemoryBackend:
    """Mem0-backed agent memory backend.

    Implements the ``MemoryBackend``, ``MemoryCapabilities``, and
    ``SharedKnowledgeStore`` protocols.

    Args:
        mem0_config: Mem0-specific backend configuration.
        max_memories_per_agent: Per-agent memory limit (from company config).
    """

    def __init__(
        self,
        *,
        mem0_config: Mem0BackendConfig,
        max_memories_per_agent: int = 10_000,
    ) -> None:
        self._mem0_config = mem0_config
        self._max_memories_per_agent = max_memories_per_agent
        self._client: Any = None
        self._connected = False
        logger.debug(
            MEMORY_BACKEND_CREATED,
            backend="mem0",
            data_dir=mem0_config.data_dir,
            collection_name=mem0_config.collection_name,
        )

    # ── Lifecycle ─────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish connection to Mem0.

        Creates the Mem0 ``Memory`` client with embedded Qdrant.

        Raises:
            MemoryConnectionError: If Mem0 initialization fails.
        """
        logger.info(MEMORY_BACKEND_CONNECTING, backend="mem0")
        try:
            from mem0 import Memory  # noqa: PLC0415

            config_dict = build_mem0_config_dict(self._mem0_config)
            client = await asyncio.to_thread(Memory.from_config, config_dict)
        except Exception as exc:
            logger.exception(
                MEMORY_BACKEND_CONNECTION_FAILED,
                backend="mem0",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to connect to Mem0: {exc}"
            raise MemoryConnectionError(msg) from exc
        self._client = client
        self._connected = True
        logger.info(MEMORY_BACKEND_CONNECTED, backend="mem0")

    async def disconnect(self) -> None:
        """Close the Mem0 connection.

        Safe to call even if not connected.
        """
        logger.info(MEMORY_BACKEND_DISCONNECTING, backend="mem0")
        self._client = None
        self._connected = False
        logger.info(MEMORY_BACKEND_DISCONNECTED, backend="mem0")

    async def health_check(self) -> bool:
        """Check whether the Mem0 backend is healthy.

        Returns:
            ``True`` if connected, ``False`` otherwise.
        """
        healthy = self._connected and self._client is not None
        logger.debug(
            MEMORY_BACKEND_HEALTH_CHECK,
            backend="mem0",
            healthy=healthy,
        )
        return healthy

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection."""
        return self._connected

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        return NotBlankStr("mem0")

    # ── Capabilities ──────────────────────────────────────────────

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]:
        """All memory categories are supported."""
        return frozenset(MemoryCategory)

    @property
    def supports_graph(self) -> bool:
        """Graph memory is not available in embedded mode."""
        return False

    @property
    def supports_temporal(self) -> bool:
        """Temporal tracking is available via timestamps."""
        return True

    @property
    def supports_vector_search(self) -> bool:
        """Vector search is available via embedded Qdrant."""
        return True

    @property
    def supports_shared_access(self) -> bool:
        """Cross-agent shared memory is available."""
        return True

    @property
    def max_memories_per_agent(self) -> int | None:
        """Maximum memories per agent from configuration."""
        return self._max_memories_per_agent

    # ── Connection guard ──────────────────────────────────────────

    def _require_connected(self) -> None:
        """Raise ``MemoryConnectionError`` if not connected."""
        if not self._connected or self._client is None:
            logger.warning(
                MEMORY_BACKEND_NOT_CONNECTED,
                backend="mem0",
            )
            msg = "Not connected — call connect() first"
            raise MemoryConnectionError(msg)

    # ── CRUD Operations ───────────────────────────────────────────

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
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the store operation fails.
        """
        self._require_connected()
        try:
            kwargs = {
                "messages": [
                    {"role": "user", "content": request.content},
                ],
                "user_id": str(agent_id),
                "metadata": build_mem0_metadata(request),
                "infer": False,
            }
            result = await asyncio.to_thread(self._client.add, **kwargs)
            results_list = result.get("results", [])
            if not results_list:
                msg = "Mem0 add returned no results"
                raise MemoryStoreError(msg)  # noqa: TRY301
            memory_id = NotBlankStr(str(results_list[0]["id"]))
        except MemoryStoreError:
            logger.exception(
                MEMORY_ENTRY_STORE_FAILED,
                agent_id=agent_id,
            )
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_ENTRY_STORE_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to store memory: {exc}"
            raise MemoryStoreError(msg) from exc
        else:
            logger.info(
                MEMORY_ENTRY_STORED,
                agent_id=agent_id,
                memory_id=memory_id,
                category=request.category.value,
            )
            return memory_id

    async def retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Retrieve memories for an agent, ordered by relevance.

        Args:
            agent_id: Owning agent identifier.
            query: Retrieval parameters.

        Returns:
            Matching memory entries ordered by relevance.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the retrieval fails.
        """
        self._require_connected()
        try:
            if query.text is not None:
                kwargs = query_to_mem0_search_args(str(agent_id), query)
                raw_result = await asyncio.to_thread(self._client.search, **kwargs)
            else:
                kwargs = query_to_mem0_getall_args(str(agent_id), query)
                raw_result = await asyncio.to_thread(self._client.get_all, **kwargs)
            raw_list = raw_result.get("results", [])
            entries = tuple(
                mem0_result_to_entry(item, str(agent_id)) for item in raw_list
            )
            entries = apply_post_filters(entries, query)
        except MemoryRetrievalError:
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_ENTRY_RETRIEVAL_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to retrieve memories: {exc}"
            raise MemoryRetrievalError(msg) from exc
        else:
            logger.info(
                MEMORY_ENTRY_RETRIEVED,
                agent_id=agent_id,
                count=len(entries),
            )
            return entries

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
            The memory entry, or ``None`` if not found.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the backend query fails.
        """
        self._require_connected()
        try:
            raw = await asyncio.to_thread(self._client.get, str(memory_id))
            if raw is None:
                logger.debug(
                    MEMORY_ENTRY_FETCHED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    found=False,
                )
                return None
            entry = mem0_result_to_entry(raw, str(agent_id))
        except MemoryRetrievalError:
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_ENTRY_FETCH_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to get memory {memory_id}: {exc}"
            raise MemoryRetrievalError(msg) from exc
        else:
            logger.debug(
                MEMORY_ENTRY_FETCHED,
                agent_id=agent_id,
                memory_id=memory_id,
                found=True,
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
            ``True`` if the entry was deleted, ``False`` if not found.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the delete operation fails.
        """
        self._require_connected()
        try:
            # Check existence first — Mem0 delete doesn't indicate
            # whether the entry existed.
            existing = await asyncio.to_thread(self._client.get, str(memory_id))
            if existing is None:
                logger.debug(
                    MEMORY_ENTRY_DELETED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    found=False,
                )
                return False
            await asyncio.to_thread(self._client.delete, str(memory_id))
        except MemoryStoreError:
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_ENTRY_DELETE_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to delete memory {memory_id}: {exc}"
            raise MemoryStoreError(msg) from exc
        else:
            logger.info(
                MEMORY_ENTRY_DELETED,
                agent_id=agent_id,
                memory_id=memory_id,
                found=True,
            )
            return True

    async def count(
        self,
        agent_id: NotBlankStr,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        """Count memory entries for an agent.

        Note: This uses ``get_all()`` internally, which is O(n).
        Acceptable because ``count()`` is not on the hot path.

        Args:
            agent_id: Owning agent identifier.
            category: Optional category filter.

        Returns:
            Number of matching entries.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the count query fails.
        """
        self._require_connected()
        try:
            raw_result = await asyncio.to_thread(
                self._client.get_all,
                user_id=str(agent_id),
                limit=self._max_memories_per_agent,
            )
            raw_list = raw_result.get("results", [])
            if category is None:
                count = len(raw_list)
            else:
                count = sum(
                    1 for item in raw_list if _extract_category(item) == category
                )
        except MemoryRetrievalError:
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_ENTRY_COUNT_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to count memories: {exc}"
            raise MemoryRetrievalError(msg) from exc
        else:
            logger.info(
                MEMORY_ENTRY_COUNTED,
                agent_id=agent_id,
                count=count,
                category=category.value if category else None,
            )
            return count

    # ── SharedKnowledgeStore ──────────────────────────────────────

    async def publish(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Publish a memory to the shared knowledge store.

        Uses a reserved namespace (``__synthorg_shared__``) and
        records the publisher in metadata for ownership tracking.

        Args:
            agent_id: Publishing agent identifier.
            request: Memory content and metadata.

        Returns:
            The backend-assigned shared memory ID.

        Raises:
            MemoryStoreError: If the publish operation fails.
        """
        self._require_connected()
        try:
            metadata = build_mem0_metadata(request)
            metadata[_PUBLISHER_KEY] = str(agent_id)
            kwargs = {
                "messages": [
                    {"role": "user", "content": request.content},
                ],
                "user_id": _SHARED_NAMESPACE,
                "metadata": metadata,
                "infer": False,
            }
            result = await asyncio.to_thread(self._client.add, **kwargs)
            results_list = result.get("results", [])
            if not results_list:
                msg = "Mem0 add returned no results for shared publish"
                raise MemoryStoreError(msg)  # noqa: TRY301
            memory_id = NotBlankStr(str(results_list[0]["id"]))
        except MemoryStoreError:
            logger.exception(
                MEMORY_SHARED_PUBLISH_FAILED,
                agent_id=agent_id,
            )
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_SHARED_PUBLISH_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to publish shared memory: {exc}"
            raise MemoryStoreError(msg) from exc
        else:
            logger.info(
                MEMORY_SHARED_PUBLISHED,
                agent_id=agent_id,
                memory_id=memory_id,
            )
            return memory_id

    async def search_shared(
        self,
        query: MemoryQuery,
        *,
        exclude_agent: NotBlankStr | None = None,
    ) -> tuple[MemoryEntry, ...]:
        """Search the shared knowledge store across agents.

        Args:
            query: Search parameters.
            exclude_agent: Optional agent ID to exclude from results.

        Returns:
            Matching shared memory entries ordered by relevance.

        Raises:
            MemoryRetrievalError: If the search fails.
        """
        self._require_connected()
        try:
            if query.text is not None:
                raw_result = await asyncio.to_thread(
                    self._client.search,
                    query=str(query.text),
                    user_id=_SHARED_NAMESPACE,
                    limit=query.limit,
                )
            else:
                raw_result = await asyncio.to_thread(
                    self._client.get_all,
                    user_id=_SHARED_NAMESPACE,
                    limit=query.limit,
                )
            raw_list = raw_result.get("results", [])

            entries: list[MemoryEntry] = []
            for item in raw_list:
                publisher = _extract_publisher(item)
                entry = mem0_result_to_entry(
                    item,
                    publisher or _SHARED_NAMESPACE,
                )
                entries.append(entry)

            result = tuple(entries)
            result = apply_post_filters(result, query)

            if exclude_agent is not None:
                result = tuple(e for e in result if e.agent_id != exclude_agent)
        except MemoryRetrievalError:
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_SHARED_SEARCH_FAILED,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to search shared knowledge: {exc}"
            raise MemoryRetrievalError(msg) from exc
        else:
            logger.info(
                MEMORY_SHARED_SEARCHED,
                count=len(result),
                exclude_agent=exclude_agent,
            )
            return result

    async def retract(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Remove a memory from the shared knowledge store.

        Verifies publisher ownership before deletion.

        Args:
            agent_id: Retracting agent identifier.
            memory_id: Shared memory identifier.

        Returns:
            ``True`` if retracted, ``False`` if not found.

        Raises:
            MemoryStoreError: If the retraction operation fails.
        """
        self._require_connected()
        try:
            raw = await asyncio.to_thread(self._client.get, str(memory_id))
            if raw is None:
                logger.debug(
                    MEMORY_SHARED_RETRACTED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    found=False,
                )
                return False

            publisher = _extract_publisher(raw)
            if publisher != str(agent_id):
                logger.warning(
                    MEMORY_SHARED_RETRACT_FAILED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    reason="ownership mismatch",
                    publisher=publisher,
                )
                msg = (
                    f"Agent {agent_id} cannot retract memory "
                    f"{memory_id} published by {publisher}"
                )
                raise MemoryStoreError(msg)  # noqa: TRY301

            await asyncio.to_thread(self._client.delete, str(memory_id))
        except MemoryStoreError:
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_SHARED_RETRACT_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to retract shared memory {memory_id}: {exc}"
            raise MemoryStoreError(msg) from exc
        else:
            logger.info(
                MEMORY_SHARED_RETRACTED,
                agent_id=agent_id,
                memory_id=memory_id,
                found=True,
            )
            return True


# ── Module-level helpers ──────────────────────────────────────────


def _extract_category(raw: dict[str, Any]) -> MemoryCategory:
    """Extract the memory category from a Mem0 result dict."""
    metadata = raw.get("metadata", {})
    if not metadata:
        return MemoryCategory.WORKING
    cat_str = metadata.get("_synthorg_category")
    if cat_str:
        return MemoryCategory(cat_str)
    return MemoryCategory.WORKING


def _extract_publisher(raw: dict[str, Any]) -> str | None:
    """Extract the publisher agent ID from a shared memory dict."""
    metadata = raw.get("metadata", {})
    if not metadata:
        return None
    value: str | None = metadata.get(_PUBLISHER_KEY)
    return value
