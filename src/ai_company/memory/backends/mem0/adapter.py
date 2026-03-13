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
    _PUBLISHER_KEY,
    apply_post_filters,
    build_mem0_metadata,
    extract_category,
    extract_publisher,
    mem0_result_to_entry,
    query_to_mem0_getall_args,
    query_to_mem0_search_args,
    validate_add_result,
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

    # ── Lifecycle ─────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish connection to Mem0.

        Creates the Mem0 ``Memory`` client with embedded Qdrant.

        Raises:
            MemoryConnectionError: If Mem0 is not installed or
                initialization fails.
        """
        logger.info(MEMORY_BACKEND_CONNECTING, backend="mem0")
        try:
            from mem0 import Memory  # noqa: PLC0415
        except ImportError as exc:
            logger.warning(
                MEMORY_BACKEND_CONNECTION_FAILED,
                backend="mem0",
                error=str(exc),
                error_type="ImportError",
            )
            msg = "mem0 package is not installed"
            raise MemoryConnectionError(msg) from exc
        try:
            config_dict = build_mem0_config_dict(self._mem0_config)
            client = await asyncio.to_thread(Memory.from_config, config_dict)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
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

        Probes the backend with a lightweight ``get_all`` call to
        verify the connection is functional, not just flagged as
        connected.

        Returns:
            ``True`` if the backend responds, ``False`` otherwise.
        """
        if not self._connected or self._client is None:
            logger.debug(
                MEMORY_BACKEND_HEALTH_CHECK,
                backend="mem0",
                healthy=False,
            )
            return False
        try:
            await asyncio.to_thread(
                self._client.get_all,
                user_id=_SHARED_NAMESPACE,
                limit=1,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                MEMORY_BACKEND_HEALTH_CHECK,
                backend="mem0",
                healthy=False,
            )
            return False
        logger.debug(
            MEMORY_BACKEND_HEALTH_CHECK,
            backend="mem0",
            healthy=True,
        )
        return True

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
            memory_id = validate_add_result(result, context="store")
        except MemoryStoreError as exc:
            logger.warning(
                MEMORY_ENTRY_STORE_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type="MemoryStoreError",
            )
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
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

        Uses ``search()`` when ``query.text`` is set, otherwise falls
        back to ``get_all()`` for unfiltered retrieval.

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
        except MemoryRetrievalError as exc:
            logger.warning(
                MEMORY_ENTRY_RETRIEVAL_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type="MemoryRetrievalError",
            )
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
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
        except MemoryRetrievalError as exc:
            logger.warning(
                MEMORY_ENTRY_FETCH_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                error=str(exc),
                error_type="MemoryRetrievalError",
            )
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
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
        except MemoryStoreError as exc:
            logger.warning(
                MEMORY_ENTRY_DELETE_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                error=str(exc),
                error_type="MemoryStoreError",
            )
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
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

        Uses ``get_all()`` internally — O(n) in the agent's memory
        count.  Acceptable because ``count()`` is not on the hot path.

        .. note::
           Results are capped at ``max_memories_per_agent``.  If an
           agent has more memories than this limit the count will be
           an underestimate.  This is consistent with the adapter's
           store/retrieve semantics which also respect the cap.

        Args:
            agent_id: Owning agent identifier.
            category: Optional category filter.

        Returns:
            Number of matching entries (capped at
            ``max_memories_per_agent``).

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
                total = len(raw_list)
            else:
                total = sum(
                    1 for item in raw_list if extract_category(item) == category
                )
        except MemoryRetrievalError as exc:
            logger.warning(
                MEMORY_ENTRY_COUNT_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type="MemoryRetrievalError",
            )
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
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
                count=total,
                category=category.value if category else None,
            )
            return total

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
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the publish operation fails.
        """
        self._require_connected()
        try:
            metadata = {
                **build_mem0_metadata(request),
                _PUBLISHER_KEY: str(agent_id),
            }
            kwargs = {
                "messages": [
                    {"role": "user", "content": request.content},
                ],
                "user_id": _SHARED_NAMESPACE,
                "metadata": metadata,
                "infer": False,
            }
            result = await asyncio.to_thread(self._client.add, **kwargs)
            memory_id = validate_add_result(result, context="shared publish")
        except MemoryStoreError as exc:
            logger.warning(
                MEMORY_SHARED_PUBLISH_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type="MemoryStoreError",
            )
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
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
            MemoryConnectionError: If the backend is not connected.
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

            raw_entries = tuple(
                mem0_result_to_entry(
                    item,
                    extract_publisher(item) or _SHARED_NAMESPACE,
                )
                for item in raw_list
            )
            filtered = apply_post_filters(raw_entries, query)

            if exclude_agent is not None:
                filtered = tuple(e for e in filtered if e.agent_id != exclude_agent)
        except MemoryRetrievalError as exc:
            logger.warning(
                MEMORY_SHARED_SEARCH_FAILED,
                error=str(exc),
                error_type="MemoryRetrievalError",
                query_text=query.text,
                exclude_agent=exclude_agent,
            )
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MEMORY_SHARED_SEARCH_FAILED,
                error=str(exc),
                error_type=type(exc).__name__,
                query_text=query.text,
                exclude_agent=exclude_agent,
            )
            msg = f"Failed to search shared knowledge: {exc}"
            raise MemoryRetrievalError(msg) from exc
        else:
            logger.info(
                MEMORY_SHARED_SEARCHED,
                count=len(filtered),
                exclude_agent=exclude_agent,
            )
            return filtered

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
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the retraction operation fails or
                ownership verification fails.
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

            publisher = extract_publisher(raw)
            if publisher is None:
                logger.warning(
                    MEMORY_SHARED_RETRACT_FAILED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    reason="not a shared memory entry (no publisher)",
                )
                msg = (
                    f"Memory {memory_id} is not a shared memory entry "
                    f"(no publisher metadata)"
                )
                raise MemoryStoreError(msg)  # noqa: TRY301

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
        except MemoryStoreError as exc:
            logger.warning(
                MEMORY_SHARED_RETRACT_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                error=str(exc),
                error_type="MemoryStoreError",
            )
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
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
