"""Shared-knowledge delegation mixin for ``Mem0MemoryBackend``.

Thin wrappers around ``shared.py`` and ``sparse_search.py`` that
validate preconditions (connection, agent ID) and forward the call.
Relies on ``_sparse_encoder``, ``_qdrant_client``, ``_mem0_config``,
``supports_sparse_search``, ``_require_connected``, and
``_validate_agent_id`` declared on the concrete class.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.mem0.client_protocol import Mem0Client
from synthorg.memory.backends.mem0.config import Mem0BackendConfig
from synthorg.memory.backends.mem0.shared import (
    publish_shared,
    retract_shared,
    search_shared_memories,
)
from synthorg.memory.backends.mem0.sparse_search import async_retrieve_sparse
from synthorg.memory.errors import MemoryError as DomainMemoryError
from synthorg.memory.errors import MemoryRetrievalError
from synthorg.memory.models import MemoryEntry, MemoryQuery, MemoryStoreRequest
from synthorg.memory.sparse import BM25Tokenizer

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


class Mem0AdapterSharedMixin:
    """Shared-knowledge-store + sparse-retrieval delegation."""

    __slots__ = ()

    _sparse_encoder: BM25Tokenizer | None
    _qdrant_client: QdrantClient | None
    _mem0_config: Mem0BackendConfig

    @property
    def supports_sparse_search(self) -> bool:  # pragma: no cover - see concrete
        """Whether BM25 sparse search is available (implemented on concrete class).

        Raises:
            NotImplementedError: If the subclass does not implement this operation.
        """
        raise NotImplementedError

    def _require_connected(self) -> Mem0Client:  # pragma: no cover - see concrete
        """Require connected.

        Returns:
            Result of type ``Mem0Client``.

        Raises:
            NotImplementedError: If the subclass does not implement this operation.
        """
        raise NotImplementedError

    def _validate_agent_id(
        self,
        agent_id: NotBlankStr,
        *,
        error_cls: type[DomainMemoryError] = MemoryRetrievalError,
    ) -> None:  # pragma: no cover - see concrete
        """Validate agent id.

        Raises:
            NotImplementedError: If the subclass does not implement this operation.
        """
        raise NotImplementedError

    async def retrieve_sparse(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Retrieve memories via BM25 sparse search (delegates to sparse_search).

        Returns:
            Tuple of ``MemoryEntry``.
        """
        if (
            not self.supports_sparse_search
            or self._sparse_encoder is None
            or self._qdrant_client is None
        ):
            return ()
        self._require_connected()
        self._validate_agent_id(agent_id, error_cls=MemoryRetrievalError)
        return await async_retrieve_sparse(
            self._sparse_encoder,
            self._qdrant_client,
            self._mem0_config.collection_name,
            agent_id,
            query,
        )

    async def publish(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Publish a memory to the shared knowledge store.

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
        return await publish_shared(client, agent_id, request)

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
        return await search_shared_memories(
            client,
            query,
            exclude_agent=exclude_agent,
        )

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
        return await retract_shared(client, agent_id, memory_id)
