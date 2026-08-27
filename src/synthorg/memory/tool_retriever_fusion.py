# module-kind: code
"""What a memory tool search actually reads.

Owns the one question, answered here and nowhere else: three retrieval sites
in the reformulation loop (the unreformulated path, the first read, and each
reformulated round) all come through :meth:`SharedMemoryFusionMixin._retrieve_fused`.

While each read the agent's own backend alone, an agent was shown org
knowledge in its injected context and then handed a tool that could not reach
it. A newly-hired agent owns no memories at all, so the tool answered "No
memories found." to every query it was ever given: measured across a live run,
44 of 44 calls, while the injection path returned 4 to 8 rows from the same
store, for the same agent, three seconds apart. An agent that searches, gets
nothing, rephrases and searches again is behaving correctly; it was the tool
that could not answer.
"""

import asyncio
import builtins

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.namespace_scope import ambient_read_namespaces
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.memory.tool_retriever_helpers import _truncate_entries, merge_results
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import MEMORY_RETRIEVAL_DEGRADED

logger = get_logger(__name__)


class SharedMemoryFusionMixin:
    """Fuses an agent's own memory with the org knowledge it may read."""

    __slots__ = ()

    _backend: MemoryBackend
    _config: MemoryRetrievalConfig
    _shared_store: SharedKnowledgeStore | None

    async def _retrieve_fused(
        self,
        *,
        agent_id: str,
        query_text: str,
        limit: int,
        categories: frozenset[MemoryCategory] | None,
    ) -> tuple[MemoryEntry, ...]:
        """Read the agent's own memory fused with org knowledge.

        The two halves take DIFFERENT error postures on purpose. The
        personal read propagates, so the caller answers "search
        unavailable" rather than "no memories found": an agent told the
        store is empty stops asking, which is the worse of the two wrong
        answers. The shared read is additive, so its failure degrades to
        personal-only and is logged rather than losing the half that did
        answer.

        Returns:
            Personal and shared entries merged by id, best first, cut to
            *limit*.

        Raises:
            MemoryError: If a critical system error occurs.
            RecursionError: If a critical system error occurs.
            CancelledError: If the task is cancelled.
        """
        query = MemoryQuery(
            text=query_text,
            limit=limit,
            categories=categories,
            namespaces=ambient_read_namespaces(),
        )
        personal = await self._backend.retrieve(NotBlankStr(agent_id), query)
        shared = await self._shared_entries(
            agent_id=agent_id,
            query=query,
        )
        if not shared:
            return personal
        return _truncate_entries(merge_results(personal, shared), limit)

    async def _shared_entries(
        self,
        *,
        agent_id: str,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Read org knowledge for *query*, degrading to empty on failure.

        Returns:
            The shared entries, or empty when the store is absent, the
            config excludes it, or the read failed.

        Raises:
            MemoryError: If a critical system error occurs.
            RecursionError: If a critical system error occurs.
            CancelledError: If the task is cancelled.
        """
        store = self._shared_store
        if store is None or not self._config.include_shared:
            return ()
        try:
            return await store.search_shared(query, exclude_agent=agent_id)
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source="shared",
                agent_id=agent_id,
                error_type="system",
                reason="system_error_in_shared_search",
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MEMORY_RETRIEVAL_DEGRADED,
                source="shared",
                agent_id=agent_id,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
                reason="org_knowledge_unavailable_personal_only",
            )
            return ()


__all__ = ["SharedMemoryFusionMixin"]
