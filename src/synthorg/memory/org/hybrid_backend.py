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
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.org_memory import (
    ORG_MEMORY_BACKEND_CONNECTED,
    ORG_MEMORY_BACKEND_DISCONNECTED,
    ORG_MEMORY_NOT_CONNECTED,
    ORG_MEMORY_POLICIES_LISTED,
    ORG_MEMORY_QUERY_COMPLETE,
    ORG_MEMORY_QUERY_FAILED,
    ORG_MEMORY_QUERY_START,
    ORG_MEMORY_WRITE_COMPLETE,
    ORG_MEMORY_WRITE_FAILED,
    ORG_MEMORY_WRITE_START,
)
from synthorg.persistence.memory_protocol import (
    OrgFactRepository,
)

logger = get_logger(__name__)

_HUMAN_AUTHOR = OrgFactAuthor(is_human=True)


class HybridPromptRetrievalBackend:
    """Hybrid prompt + retrieval organizational memory backend.

    Core policies are static strings that get injected directly into
    agent system prompts.  Extended facts are stored in a dynamic
    ``OrgFactRepository`` for on-demand retrieval.

    Args:
        core_policies: Static core policy texts.
        store: Extended facts store implementation.
        access_config: Write access control configuration.
    """

    def __init__(
        self,
        *,
        core_policies: tuple[NotBlankStr, ...],
        store: OrgFactRepository,
        access_config: WriteAccessConfig,
    ) -> None:
        self._core_policies = core_policies
        self._store = store
        self._access_config = access_config
        self._connected = False

    async def connect(self) -> None:
        """Mark the backend live; store lifecycle is owned by persistence.

        After A4 the extended ``OrgFactRepository`` is a repository on the
        shared :class:`PersistenceBackend`, whose connection is opened
        earlier in the startup sequence.  Connecting/disconnecting the
        store here would double-close it on shutdown.
        """
        self._connected = True
        logger.info(
            ORG_MEMORY_BACKEND_CONNECTED,
            backend="hybrid_prompt_retrieval",
        )

    async def disconnect(self) -> None:
        """Release the backend without touching the shared store."""
        self._connected = False
        logger.info(
            ORG_MEMORY_BACKEND_DISCONNECTED,
            backend="hybrid_prompt_retrieval",
        )

    async def health_check(self) -> bool:
        """Return the local connection flag.

        Persistence-backed repositories do not expose a per-store
        ``is_connected`` probe (and should not -- the pool is health-
        checked by the main backend).

        Returns:
            ``True`` when the backend is connected (the value of
            ``self._connected``), ``False`` otherwise.
        """
        return self._connected

    @property
    def is_connected(self) -> bool:
        """Whether the backend is connected."""
        return self._connected

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend name."""
        return NotBlankStr("hybrid_prompt_retrieval")

    def _require_connected(self) -> None:
        """Raise if not connected.

        Raises:
            OrgMemoryConnectionError: If the related operation fails.
        """
        if not self._connected:
            msg = "Not connected -- call connect() first"
            logger.warning(ORG_MEMORY_NOT_CONNECTED, backend="hybrid_prompt_retrieval")
            raise OrgMemoryConnectionError(msg)

    async def list_policies(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[OrgFact, ...]:
        """Return core policies, optionally paginated.

        Static policies (from ``core_policies`` config) are returned
        first as synthetic ``OrgFact`` objects. Dynamically written
        ``CORE_POLICY`` facts stored in the extended store follow.
        When ``limit`` is None the full set is returned; otherwise the
        combined sequence is sliced by ``offset`` and ``limit``.

        Returns:
            Core policy facts with category ``CORE_POLICY`` (full or
            sliced view).

        Raises:
            OrgMemoryConnectionError: If the backend is not connected.
            OrgMemoryQueryError: If the related operation fails.
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
            )
            for i, policy in enumerate(self._core_policies)
        )
        try:
            dynamic = await self._store.list_by_category(OrgFactCategory.CORE_POLICY)
        except OrgMemoryQueryError as exc:
            log_exception_redacted(
                logger,
                ORG_MEMORY_QUERY_FAILED,
                exc,
                operation="list_policies",
                category=OrgFactCategory.CORE_POLICY.value,
            )
            raise
        facts = static + dynamic
        if limit is None and offset == 0:
            logger.debug(ORG_MEMORY_POLICIES_LISTED, count=len(facts))
            return facts
        offset = max(0, offset)
        end = None if limit is None else offset + max(0, limit)
        page = facts[offset:end]
        logger.debug(ORG_MEMORY_POLICIES_LISTED, count=len(page))
        return page

    async def count_policies(self) -> int:
        """Return the unfiltered count of core policy facts.

        Returns:
            Result of type ``int``.

        Raises:
            OrgMemoryConnectionError: If the backend is not connected.
            OrgMemoryQueryError: If the related operation fails.
        """
        self._require_connected()
        try:
            dynamic = await self._store.list_by_category(OrgFactCategory.CORE_POLICY)
        except OrgMemoryQueryError as exc:
            log_exception_redacted(
                logger,
                ORG_MEMORY_QUERY_FAILED,
                exc,
                operation="count_policies",
                category=OrgFactCategory.CORE_POLICY.value,
            )
            raise
        return len(self._core_policies) + len(dynamic)

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
        except OrgMemoryQueryError as exc:
            log_exception_redacted(logger, ORG_MEMORY_QUERY_FAILED, exc)
            raise
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

        fact = OrgFact(
            id=fact_id,
            content=request.content,
            category=request.category,
            tags=request.tags,
            author=author,
            created_at=now,
        )

        try:
            await self._store.save(fact)
        except OrgMemoryWriteError as exc:
            log_exception_redacted(
                logger, ORG_MEMORY_WRITE_FAILED, exc, fact_id=fact_id
            )
            raise
        else:
            logger.info(
                ORG_MEMORY_WRITE_COMPLETE,
                fact_id=fact_id,
            )
            return fact_id
