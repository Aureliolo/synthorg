"""Project-aware memory retrieval facade.

When an agent on project P calls memory retrieval through this facade,
the facade fan-outs to two backend retrievals in parallel:

1. The agent's own per-agent memory under *agent_id*.
2. The :attr:`MemoryCategory.PROJECT_DOC` namespace under
   :data:`SYSTEM_DOCS_AGENT_ID` scoped to project P via the
   ``project:<id>`` tag.

Results are merged by descending ``relevance_score`` and truncated to
the original query's ``limit``. This makes project docs first-class
RAG members without any special-casing in agent code: callers continue
to use ``memory.retrieve(agent_id, query)`` (post-patch) and PROJECT_DOC
hits surface alongside SEMANTIC / EPISODIC / etc.

Construction is via :func:`synthorg.docs_engine.factory.build_docs_service`.
"""

import asyncio
import builtins
from typing import TYPE_CHECKING

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_MEMORY_NAMESPACE,
    DOCS_PROJECT_TAG_PREFIX,
    SYSTEM_DOCS_AGENT_ID,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docs import (
    DOC_FACADE_FANOUT,
    DOC_FACADE_FANOUT_FAILED,
)

if TYPE_CHECKING:
    from synthorg.memory.models import MemoryEntry, MemoryQuery
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)


class ProjectAwareMemoryFacade:
    """Merges agent-scoped + project-doc-scoped retrieval results."""

    __slots__ = ("_backend",)

    def __init__(self, *, backend: MemoryBackend) -> None:
        self._backend = backend

    async def retrieve(
        self,
        *,
        agent_id: NotBlankStr,
        project_id: NotBlankStr | None,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Return *query* results merged across agent + project namespaces.

        When *project_id* is ``None`` this degrades to a plain
        per-agent retrieval (no fan-out, no merge).

        Args:
            agent_id: Calling agent's identifier.
            project_id: Owning project, or ``None`` when the agent has
                no project context.
            query: Retrieval query (text + filters).

        Returns:
            Merged entries ordered by descending ``relevance_score``,
            truncated to ``query.limit``.
        """
        if project_id is None:
            return await self._backend.retrieve(agent_id, query)
        try:
            async with asyncio.TaskGroup() as tg:
                agent_task = tg.create_task(self._backend.retrieve(agent_id, query))
                docs_task = tg.create_task(
                    self._backend.retrieve(
                        SYSTEM_DOCS_AGENT_ID,
                        _project_doc_query(project_id=project_id, base=query),
                    )
                )
        except builtins.BaseExceptionGroup as group:
            if group.subgroup(asyncio.CancelledError) is not None:
                raise
            logger.warning(
                DOC_FACADE_FANOUT_FAILED,
                agent_id=agent_id,
                project_id=project_id,
                exceptions=tuple(type(exc).__name__ for exc in group.exceptions),
                error=safe_error_description(group.exceptions[0])
                if group.exceptions
                else "no exceptions",
            )
            return await self._backend.retrieve(agent_id, query)
        merged = _merge_by_score(
            agent_task.result(),
            docs_task.result(),
            limit=query.limit,
        )
        logger.debug(
            DOC_FACADE_FANOUT,
            agent_id=agent_id,
            project_id=project_id,
            agent_hits=len(agent_task.result()),
            doc_hits=len(docs_task.result()),
            merged=len(merged),
        )
        return merged


def _project_doc_query(*, project_id: NotBlankStr, base: MemoryQuery) -> MemoryQuery:
    """Build the project-doc-scoped sibling query from *base*."""
    project_tag = NotBlankStr(f"{DOCS_PROJECT_TAG_PREFIX}{project_id}")
    return base.model_copy(
        update={
            "categories": frozenset({MemoryCategory.PROJECT_DOC}),
            "namespaces": frozenset({DOCS_MEMORY_NAMESPACE}),
            "tags": (*base.tags, project_tag),
        }
    )


def _merge_by_score(
    primary: tuple[MemoryEntry, ...],
    secondary: tuple[MemoryEntry, ...],
    *,
    limit: int,
) -> tuple[MemoryEntry, ...]:
    """Interleave by descending ``relevance_score`` and truncate."""
    combined = list(primary) + list(secondary)
    combined.sort(
        key=lambda entry: (
            entry.relevance_score if entry.relevance_score is not None else 0.0
        ),
        reverse=True,
    )
    return tuple(combined[:limit])
