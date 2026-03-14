"""Hybrid prompt + retrieval org memory backend.

Combines static core policies (injected into prompts) with a
dynamic extended store for searchable organizational facts.
"""

import uuid
from datetime import UTC, datetime

from synthorg.core.enums import OrgFactCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.org.access_control import WriteAccessConfig, require_write_access
from synthorg.memory.org.errors import (
    OrgMemoryConnectionError,
    OrgMemoryQueryError,
    OrgMemoryWriteError,
)
from synthorg.memory.org.models import (
    OrgFact,
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)
from synthorg.memory.org.store import OrgFactStore  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.org_memory import (
    ORG_MEMORY_CONNECT_FAILED,
    ORG_MEMORY_NOT_CONNECTED,
    ORG_MEMORY_POLICIES_LISTED,
    ORG_MEMORY_QUERY_COMPLETE,
    ORG_MEMORY_QUERY_FAILED,
    ORG_MEMORY_QUERY_START,
    ORG_MEMORY_WRITE_COMPLETE,
    ORG_MEMORY_WRITE_FAILED,
    ORG_MEMORY_WRITE_START,
)

logger = get_logger(__name__)

_HUMAN_AUTHOR = OrgFactAuthor(is_human=True)


class HybridPromptRetrievalBackend:
    """Hybrid prompt + retrieval organizational memory backend.

    Core policies are static strings that get injected directly into
    agent system prompts.  Extended facts are stored in a dynamic
    ``OrgFactStore`` for on-demand retrieval.

    Args:
        core_policies: Static core policy texts.
        store: Extended facts store implementation.
        access_config: Write access control configuration.
    """

    def __init__(
        self,
        *,
        core_policies: tuple[NotBlankStr, ...],
        store: OrgFactStore,
        access_config: WriteAccessConfig,
    ) -> None:
        self._core_policies = core_policies
        self._store = store
        self._access_config = access_config
        self._connected = False

    async def connect(self) -> None:
        """Connect the underlying store.

        Raises:
            OrgMemoryConnectionError: If the store connection fails.
        """
        try:
            await self._store.connect()
        except OrgMemoryConnectionError:
            logger.exception(
                ORG_MEMORY_CONNECT_FAILED,
                backend="hybrid_prompt_retrieval",
                reason="store connection failed",
            )
            raise
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect the underlying store."""
        try:
            await self._store.disconnect()
        finally:
            self._connected = False

    async def health_check(self) -> bool:
        """Check store connectivity by delegating to the store.

        Returns:
            ``True`` if connected and the store reports connected,
            ``False`` otherwise.
        """
        return self._connected and self._store.is_connected

    @property
    def is_connected(self) -> bool:
        """Whether the backend is connected."""
        return self._connected

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend name."""
        return NotBlankStr("hybrid_prompt_retrieval")

    def _require_connected(self) -> None:
        """Raise if not connected."""
        if not self._connected:
            msg = "Not connected — call connect() first"
            logger.warning(ORG_MEMORY_NOT_CONNECTED, backend="hybrid_prompt_retrieval")
            raise OrgMemoryConnectionError(msg)

    async def list_policies(self) -> tuple[OrgFact, ...]:
        """Return all core policies — static config *and* dynamically written.

        Static policies (from ``core_policies`` config) are returned
        first as synthetic ``OrgFact`` objects.  Dynamically written
        ``CORE_POLICY`` facts stored in the extended store follow.

        Returns:
            Core policy facts with category ``CORE_POLICY``.
        """
        self._require_connected()
        now = datetime.now(UTC)
        static = tuple(
            OrgFact(
                id=f"core-policy-{i}",
                content=policy,
                category=OrgFactCategory.CORE_POLICY,
                author=_HUMAN_AUTHOR,
                created_at=now,
                version=1,
            )
            for i, policy in enumerate(self._core_policies)
        )
        dynamic = await self._store.list_by_category(OrgFactCategory.CORE_POLICY)
        facts = static + dynamic
        logger.debug(ORG_MEMORY_POLICIES_LISTED, count=len(facts))
        return facts

    async def query(self, query: OrgMemoryQuery) -> tuple[OrgFact, ...]:
        """Query facts from the extended store.

        Args:
            query: Query parameters.

        Returns:
            Matching facts.

        Raises:
            OrgMemoryConnectionError: If not connected.
            OrgMemoryQueryError: If the query fails.
        """
        self._require_connected()
        logger.debug(
            ORG_MEMORY_QUERY_START,
            context=query.context,
            categories=(
                sorted(c.value for c in query.categories) if query.categories else None
            ),
            limit=query.limit,
        )
        try:
            results = await self._store.query(
                categories=query.categories,
                text=query.context,
                limit=query.limit,
            )
        except OrgMemoryQueryError:
            logger.exception(ORG_MEMORY_QUERY_FAILED)
            raise
        except Exception as exc:
            logger.exception(
                ORG_MEMORY_QUERY_FAILED,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            msg = f"Failed to query org facts: {exc}"
            raise OrgMemoryQueryError(msg) from exc
        else:
            logger.info(ORG_MEMORY_QUERY_COMPLETE, count=len(results))
            return results

    async def write(
        self,
        request: OrgFactWriteRequest,
        *,
        author: OrgFactAuthor,
    ) -> NotBlankStr:
        """Write a new organizational fact.

        Checks write access, generates an ID, and persists the fact.

        Args:
            request: Fact content and category.
            author: The author of the fact.

        Returns:
            The assigned fact ID.

        Raises:
            OrgMemoryConnectionError: If not connected.
            OrgMemoryAccessDeniedError: If write access is denied.
            OrgMemoryWriteError: If the write operation fails.
        """
        self._require_connected()
        require_write_access(self._access_config, request.category, author)

        fact_id = NotBlankStr(str(uuid.uuid4()))
        now = datetime.now(UTC)

        logger.info(
            ORG_MEMORY_WRITE_START,
            fact_id=fact_id,
            category=request.category.value,
            author_is_human=author.is_human,
            author_agent_id=author.agent_id,
        )

        version = await self._compute_next_version(request.category)

        fact = OrgFact(
            id=fact_id,
            content=request.content,
            category=request.category,
            author=author,
            created_at=now,
            version=version,
        )

        try:
            await self._store.save(fact)
        except OrgMemoryWriteError:
            logger.exception(
                ORG_MEMORY_WRITE_FAILED,
                fact_id=fact_id,
            )
            raise
        except Exception as exc:
            logger.exception(
                ORG_MEMORY_WRITE_FAILED,
                fact_id=fact_id,
                error=str(exc),
            )
            msg = f"Failed to write org fact: {exc}"
            raise OrgMemoryWriteError(msg) from exc
        else:
            logger.info(
                ORG_MEMORY_WRITE_COMPLETE,
                fact_id=fact_id,
                version=version,
            )
            return fact_id

    async def _compute_next_version(
        self,
        category: OrgFactCategory,
    ) -> int:
        """Compute the next version number for facts in this category.

        Fetches all existing facts in the category and computes
        ``max(version) + 1`` in Python.  Note: this loads all rows,
        which may be inefficient for categories with many facts.

        Concurrent writers in the same category may produce duplicate
        versions because the read-then-write is not atomic.  The
        ``OrgFactStore`` protocol does not expose transaction primitives,
        so strict uniqueness cannot be guaranteed at this layer.

        Args:
            category: The fact category.

        Returns:
            Next version number (max existing + 1, or 1 if none).

        Raises:
            OrgMemoryQueryError: If the version lookup fails.
        """
        try:
            existing = await self._store.list_by_category(category)
        except Exception as exc:
            logger.warning(
                ORG_MEMORY_QUERY_FAILED,
                operation="compute_next_version",
                category=category.value,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        if not existing:
            return 1
        return max(f.version for f in existing) + 1
