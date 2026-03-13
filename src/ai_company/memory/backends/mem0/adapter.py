"""Mem0 memory backend adapter.

Implements ``MemoryBackend``, ``MemoryCapabilities``, and
``SharedKnowledgeStore`` protocols using Mem0 as the storage layer
(default: Qdrant (embedded by default) + SQLite).

All Mem0 calls are synchronous — they run in ``asyncio.to_thread()``
to avoid blocking the event loop.

All methods re-raise ``builtins.MemoryError`` and ``RecursionError``
immediately without wrapping, to avoid masking system-level failures.

Note: This file exceeds the 800-line guideline because the single
``Mem0MemoryBackend`` class implements three protocols cohesively
(``MemoryBackend``, ``MemoryCapabilities``, ``SharedKnowledgeStore``).
Splitting would fragment the unified client lifecycle and connection
guard logic.
"""

import asyncio
import builtins
from typing import TYPE_CHECKING, Any

from ai_company.core.enums import MemoryCategory
from ai_company.core.types import NotBlankStr
from ai_company.memory.backends.mem0.config import (
    Mem0BackendConfig,
    build_mem0_config_dict,
)
from ai_company.memory.backends.mem0.mappers import (
    PUBLISHER_KEY,
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
from ai_company.memory.errors import (
    MemoryError as DomainMemoryError,
)
from ai_company.observability import get_logger
from ai_company.observability.events.memory import (
    MEMORY_BACKEND_AGENT_ID_REJECTED,
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

if TYPE_CHECKING:
    from typing import Protocol

    from ai_company.memory.models import (
        MemoryEntry,
        MemoryQuery,
        MemoryStoreRequest,
    )

    class Mem0Client(Protocol):
        """Structural type for the Mem0 ``Memory`` client.

        Defines the subset of ``Memory`` methods that the adapter
        uses, so the rest of the codebase does not depend on
        ``Any``.
        """

        def add(self, **kwargs: Any) -> dict[str, Any]:
            """Add a memory entry."""
            ...

        def search(self, **kwargs: Any) -> dict[str, Any]:
            """Search memories."""
            ...

        def get_all(self, **kwargs: Any) -> dict[str, Any]:
            """Get all memories for a user."""
            ...

        def get(self, memory_id: str) -> dict[str, Any] | None:
            """Get a single memory by ID."""
            ...

        def delete(self, memory_id: str) -> None:
            """Delete a memory by ID."""
            ...


logger = get_logger(__name__)

# Reserved user_id for the shared knowledge namespace.
# All shared memories are stored under this Mem0 ``user_id`` so they
# are isolated from per-agent memories and can be queried centrally.
_SHARED_NAMESPACE: str = "__synthorg_shared__"


def _validate_mem0_result(
    raw_result: Any,
    *,
    context: str,
) -> list[dict[str, Any]]:
    """Validate and extract the results list from a Mem0 response.

    Args:
        raw_result: Raw return value from a Mem0 SDK call.
        context: Human-readable context for error messages.

    Returns:
        The ``"results"`` list from the response.

    Raises:
        MemoryRetrievalError: If the response is not a dict or
            ``"results"`` is not a list.
    """
    if not isinstance(raw_result, dict):
        msg = (
            f"Unexpected Mem0 response type for {context}: "
            f"{type(raw_result).__name__}, expected dict"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    if "results" not in raw_result:
        msg = (
            f"Mem0 response missing 'results' key for {context}: "
            f"keys={list(raw_result.keys())}"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    raw_list = raw_result["results"]
    if not isinstance(raw_list, list):
        msg = (
            f"Unexpected Mem0 results type for {context}: "
            f"{type(raw_list).__name__}, expected list"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    return raw_list


def _resolve_publisher(item: dict[str, Any]) -> str:
    """Extract publisher from a shared memory, defaulting to namespace.

    Logs at DEBUG when publisher metadata is missing.
    """
    publisher = extract_publisher(item)
    if publisher is None:
        logger.debug(
            MEMORY_SHARED_SEARCHED,
            memory_id=item.get("id", "?"),
            reason="no publisher metadata — attributing to shared namespace",
        )
        return _SHARED_NAMESPACE
    return publisher


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
        self._client: Mem0Client | None = None
        self._connected = False
        self._connect_lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish connection to Mem0.

        Creates the Mem0 ``Memory`` client with Qdrant
        (embedded by default).  Idempotent — returns immediately
        if already connected.  Uses a lock to prevent concurrent
        ``connect()`` calls from creating duplicate clients.

        Raises:
            MemoryConnectionError: If Mem0 is not installed or
                initialization fails.
        """
        if self._connected:
            return
        async with self._connect_lock:
            # Double-check after acquiring the lock — another
            # coroutine may have connected while we waited.
            if self._connected:
                return  # type: ignore[unreachable]  # concurrent state change
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
            except builtins.MemoryError, RecursionError:
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

        Releases the client reference so the garbage collector can
        reclaim resources.  Safe to call even if not connected.
        Acquires ``_connect_lock`` to prevent racing with an
        in-progress ``connect()`` call.
        """
        async with self._connect_lock:
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
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MEMORY_BACKEND_HEALTH_CHECK,
                backend="mem0",
                healthy=False,
                error=str(exc),
                error_type=type(exc).__name__,
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

    # ── Guards ────────────────────────────────────────────────────

    def _require_connected(self) -> Mem0Client:
        """Return the client or raise ``MemoryConnectionError``.

        Returns:
            The connected Mem0 client (enables mypy type narrowing).
        """
        if not self._connected or self._client is None:
            logger.warning(
                MEMORY_BACKEND_NOT_CONNECTED,
                backend="mem0",
            )
            msg = "Not connected — call connect() first"
            raise MemoryConnectionError(msg)
        return self._client

    def _validate_agent_id(
        self,
        agent_id: NotBlankStr,
        *,
        error_cls: type[DomainMemoryError] = MemoryStoreError,
    ) -> None:
        """Reject the reserved shared namespace as an agent ID.

        Args:
            agent_id: Agent identifier to validate.
            error_cls: Error class to raise on rejection — defaults to
                ``MemoryStoreError`` for write ops, pass
                ``MemoryRetrievalError`` or ``MemoryConnectionError``
                for read/connection ops.

        Raises:
            MemoryStoreError: If ``agent_id`` collides with
                ``_SHARED_NAMESPACE`` (default).
            MemoryRetrievalError: If ``error_cls`` was set to
                ``MemoryRetrievalError``.
        """
        if str(agent_id) == _SHARED_NAMESPACE:
            logger.warning(
                MEMORY_BACKEND_AGENT_ID_REJECTED,
                agent_id=agent_id,
                reason="reserved shared namespace",
            )
            msg = (
                f"agent_id must not be the reserved shared namespace: "
                f"{_SHARED_NAMESPACE!r}"
            )
            raise error_cls(msg)

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
        client = self._require_connected()
        self._validate_agent_id(agent_id)
        try:
            kwargs = {
                "messages": [
                    {"role": "user", "content": request.content},
                ],
                "user_id": str(agent_id),
                "metadata": build_mem0_metadata(request),
                "infer": False,
            }
            result = await asyncio.to_thread(client.add, **kwargs)
            memory_id = validate_add_result(result, context="store")
        except MemoryStoreError as exc:
            logger.warning(
                MEMORY_ENTRY_STORE_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type="MemoryStoreError",
            )
            raise
        except builtins.MemoryError, RecursionError:
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
        back to ``get_all()`` for non-semantic retrieval (post-filters
        still apply).

        Args:
            agent_id: Owning agent identifier.
            query: Retrieval parameters.

        Returns:
            Matching memory entries ordered by relevance.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the retrieval fails.
        """
        client = self._require_connected()
        self._validate_agent_id(agent_id, error_cls=MemoryRetrievalError)
        try:
            if query.text is not None:
                kwargs = query_to_mem0_search_args(str(agent_id), query)
                raw_result = await asyncio.to_thread(client.search, **kwargs)
            else:
                kwargs = query_to_mem0_getall_args(str(agent_id), query)
                raw_result = await asyncio.to_thread(client.get_all, **kwargs)
            raw_list = _validate_mem0_result(raw_result, context="retrieve")
            entries = tuple(mem0_result_to_entry(item, agent_id) for item in raw_list)
            entries = apply_post_filters(entries, query)
        except MemoryRetrievalError as exc:
            logger.warning(
                MEMORY_ENTRY_RETRIEVAL_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type="MemoryRetrievalError",
            )
            raise
        except builtins.MemoryError, RecursionError:
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

        Verifies ownership: if the retrieved memory belongs to a
        different agent the method returns ``None``.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Memory identifier.

        Returns:
            The memory entry, or ``None`` if not found or not owned.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryRetrievalError: If the backend query fails.
        """
        client = self._require_connected()
        self._validate_agent_id(agent_id, error_cls=MemoryRetrievalError)
        try:
            raw = await asyncio.to_thread(client.get, str(memory_id))
            if raw is None:
                logger.debug(
                    MEMORY_ENTRY_FETCHED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    found=False,
                )
                return None
            owner = raw.get("user_id")
            if owner is None:
                logger.warning(
                    MEMORY_ENTRY_FETCHED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    found=False,
                    reason="memory has no user_id — ownership "
                    "unverifiable, refusing to return",
                )
                return None
            if str(owner) != str(agent_id):
                logger.debug(
                    MEMORY_ENTRY_FETCHED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    found=False,
                    reason="ownership mismatch",
                    actual_owner=str(owner),
                )
                return None
            entry = mem0_result_to_entry(raw, agent_id)
        except MemoryRetrievalError as exc:
            logger.warning(
                MEMORY_ENTRY_FETCH_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                error=str(exc),
                error_type="MemoryRetrievalError",
            )
            raise
        except builtins.MemoryError, RecursionError:
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

        Verifies ownership before deletion.  Shared-namespace entries
        must be removed through ``retract()`` instead.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Memory identifier.

        Returns:
            ``True`` if the entry was deleted, ``False`` if not found.

        Raises:
            MemoryConnectionError: If the backend is not connected.
            MemoryStoreError: If the delete operation fails or
                ownership verification fails.
        """
        client = self._require_connected()
        self._validate_agent_id(agent_id)
        try:
            # Check existence first — Mem0 delete doesn't indicate
            # whether the entry existed.
            existing = await asyncio.to_thread(client.get, str(memory_id))
            if existing is None:
                logger.debug(
                    MEMORY_ENTRY_DELETED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    found=False,
                )
                return False
            # Block deletion of shared-namespace entries — use retract().
            owner = existing.get("user_id")
            if owner is None:
                msg = (
                    f"Memory {memory_id} has no user_id — ownership "
                    f"unverifiable, refusing deletion"
                )
                logger.warning(
                    MEMORY_ENTRY_DELETE_FAILED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    reason="unverifiable_ownership",
                )
                raise MemoryStoreError(msg)  # noqa: TRY301
            if str(owner) == _SHARED_NAMESPACE:
                msg = (
                    f"Memory {memory_id} belongs to the shared namespace — "
                    f"use retract() to remove shared entries"
                )
                logger.warning(
                    MEMORY_ENTRY_DELETE_FAILED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    reason="shared namespace entry",
                )
                raise MemoryStoreError(msg)  # noqa: TRY301
            # Verify ownership — reject cross-agent deletion.
            if str(owner) != str(agent_id):
                msg = (
                    f"Agent {agent_id} cannot delete memory "
                    f"{memory_id} owned by {owner}"
                )
                logger.warning(
                    MEMORY_ENTRY_DELETE_FAILED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    reason="ownership mismatch",
                    actual_owner=str(owner),
                )
                raise MemoryStoreError(msg)  # noqa: TRY301
            await asyncio.to_thread(client.delete, str(memory_id))
        except MemoryStoreError:
            raise
        except builtins.MemoryError, RecursionError:
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

        Uses ``get_all()`` internally — retrieves all of the agent's
        memories, so cost scales linearly with the agent's memory count.
        Acceptable because ``count()`` is not on the hot path.

        Note:
            Results are capped at ``max_memories_per_agent``.  If an
            agent has more memories than this limit the count will be
            an underestimate.  Truncation is detected when the raw
            result set (before any category filter) reaches
            ``max_memories_per_agent``.  This is consistent with the
            adapter's store/retrieve semantics which also respect the
            cap.

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
        client = self._require_connected()
        self._validate_agent_id(agent_id, error_cls=MemoryRetrievalError)
        try:
            raw_result = await asyncio.to_thread(
                client.get_all,
                user_id=str(agent_id),
                limit=self._max_memories_per_agent,
            )
            raw_list = _validate_mem0_result(raw_result, context="count")
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
        except builtins.MemoryError, RecursionError:
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
            truncated = len(raw_list) == self._max_memories_per_agent
            if truncated:
                logger.warning(
                    MEMORY_ENTRY_COUNTED,
                    agent_id=agent_id,
                    count=total,
                    category=category.value if category else None,
                    truncated=True,
                    reason="raw result set reached max_memories_per_agent "
                    "limit — actual count may be higher "
                    "(conservative estimate)",
                )
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
        client = self._require_connected()
        self._validate_agent_id(agent_id)
        try:
            metadata = {
                **build_mem0_metadata(request),
                PUBLISHER_KEY: str(agent_id),
            }
            kwargs = {
                "messages": [
                    {"role": "user", "content": request.content},
                ],
                "user_id": _SHARED_NAMESPACE,
                "metadata": metadata,
                "infer": False,
            }
            result = await asyncio.to_thread(client.add, **kwargs)
            memory_id = validate_add_result(result, context="shared publish")
        except MemoryStoreError as exc:
            logger.warning(
                MEMORY_SHARED_PUBLISH_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type="MemoryStoreError",
            )
            raise
        except builtins.MemoryError, RecursionError:
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
        client = self._require_connected()
        if exclude_agent is not None and str(exclude_agent) == _SHARED_NAMESPACE:
            msg = (
                "exclude_agent must not be the reserved shared namespace: "
                f"{_SHARED_NAMESPACE!r}"
            )
            logger.warning(
                MEMORY_BACKEND_AGENT_ID_REJECTED,
                agent_id=exclude_agent,
                reason="reserved shared namespace used as exclude_agent",
            )
            raise MemoryRetrievalError(msg)
        try:
            if query.text is not None:
                raw_result = await asyncio.to_thread(
                    client.search,
                    query=str(query.text),
                    user_id=_SHARED_NAMESPACE,
                    limit=query.limit,
                )
            else:
                raw_result = await asyncio.to_thread(
                    client.get_all,
                    user_id=_SHARED_NAMESPACE,
                    limit=query.limit,
                )
            raw_list = _validate_mem0_result(
                raw_result,
                context="search_shared",
            )

            raw_entries = tuple(
                mem0_result_to_entry(
                    item,
                    NotBlankStr(
                        _resolve_publisher(item),
                    ),
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
        except builtins.MemoryError, RecursionError:
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
        client = self._require_connected()
        self._validate_agent_id(agent_id)
        try:
            raw = await asyncio.to_thread(client.get, str(memory_id))
            if raw is None:
                logger.debug(
                    MEMORY_SHARED_RETRACTED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    found=False,
                )
                return False

            # Verify this memory belongs to the shared namespace.
            owner_ns = raw.get("user_id")
            if owner_ns != _SHARED_NAMESPACE:
                logger.warning(
                    MEMORY_SHARED_RETRACT_FAILED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    reason="not in shared namespace",
                    actual_namespace=str(owner_ns),
                )
                msg = (
                    f"Memory {memory_id} is not in the shared namespace — "
                    f"use delete() to remove private entries"
                )
                raise MemoryStoreError(msg)  # noqa: TRY301

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

            await asyncio.to_thread(client.delete, str(memory_id))
        except MemoryStoreError:
            # Ownership-check MemoryStoreErrors are already logged
            # with context (reason, publisher) above — re-raise
            # without duplicate logging.
            raise
        except builtins.MemoryError, RecursionError:
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
