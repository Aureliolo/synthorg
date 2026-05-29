"""Project-aware memory retrieval facade.

When an agent on project P calls memory retrieval through this facade,
the facade fans out to several backend retrievals in parallel and merges
them by descending ``relevance_score``:

1. The agent's own per-agent memory under *agent_id*.
2. The :attr:`MemoryCategory.PROJECT_DOC` namespace under
   :data:`SYSTEM_DOCS_AGENT_ID` scoped to project P (living docs).
3. When ``knowledge_enabled`` (set at the boot path), the
   :attr:`MemoryCategory.KNOWLEDGE` namespace under
   :data:`SYSTEM_KNOWLEDGE_AGENT_ID` scoped to project P *and* the global
   corpus (the knowledge + provenance substrate).

This makes both project docs and the ingested knowledge corpus
first-class RAG members without special-casing in agent code: callers
keep using ``memory.retrieve(agent_id, query)`` and the extra hits
surface alongside SEMANTIC / EPISODIC / etc. Citations for knowledge
hits are resolved on the explicit ``search_knowledge`` path; here the
hits carry their ``source:`` / ``chunk:`` tags for downstream use.

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
from synthorg.knowledge.constants import (
    KNOWLEDGE_GLOBAL_SCOPE_TAG,
    KNOWLEDGE_MEMORY_NAMESPACE,
    KNOWLEDGE_PROJECT_TAG_PREFIX,
    SYSTEM_KNOWLEDGE_AGENT_ID,
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
    """Merges agent + project-doc (+ knowledge) retrieval results."""

    __slots__ = ("_backend", "_knowledge_enabled")

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        knowledge_enabled: bool = False,
    ) -> None:
        self._backend = backend
        self._knowledge_enabled = knowledge_enabled

    async def retrieve(
        self,
        *,
        agent_id: NotBlankStr,
        project_id: NotBlankStr | None,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        """Return *query* results merged across agent + project namespaces.

        When *project_id* is ``None`` this degrades to a plain per-agent
        retrieval (no fan-out, no merge).

        Args:
            agent_id: Calling agent's identifier.
            project_id: Owning project, or ``None`` when the agent has no
                project context.
            query: Retrieval query (text + filters).

        Returns:
            Merged entries ordered by descending ``relevance_score``,
            truncated to ``query.limit``.
        """
        if project_id is None:
            return await self._backend.retrieve(agent_id, query)
        targets: list[tuple[NotBlankStr, MemoryQuery]] = [
            (agent_id, query),
            (
                SYSTEM_DOCS_AGENT_ID,
                _project_doc_query(project_id=project_id, base=query),
            ),
        ]
        if self._knowledge_enabled:
            targets.append(
                (
                    SYSTEM_KNOWLEDGE_AGENT_ID,
                    _knowledge_query(
                        scope_tag=NotBlankStr(
                            f"{KNOWLEDGE_PROJECT_TAG_PREFIX}{project_id}"
                        ),
                        base=query,
                    ),
                )
            )
            targets.append(
                (
                    SYSTEM_KNOWLEDGE_AGENT_ID,
                    _knowledge_query(scope_tag=KNOWLEDGE_GLOBAL_SCOPE_TAG, base=query),
                )
            )
        tasks: list[asyncio.Task[tuple[MemoryEntry, ...]]] = []
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(self._backend.retrieve(target_id, target_query))
                    for target_id, target_query in targets
                ]
        except builtins.BaseExceptionGroup as group:
            if group.subgroup(asyncio.CancelledError) is not None:
                raise
            if (
                group.subgroup(builtins.MemoryError) is not None
                or group.subgroup(builtins.RecursionError) is not None
            ):
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
            agent_task = tasks[0] if tasks else None
            if (
                agent_task is not None
                and agent_task.done()
                and not agent_task.cancelled()
                and agent_task.exception() is None
            ):
                return agent_task.result()
            return await self._backend.retrieve(agent_id, query)
        results = tuple(task.result() for task in tasks)
        merged = _merge_by_score(*results, limit=query.limit)
        logger.debug(
            DOC_FACADE_FANOUT,
            agent_id=agent_id,
            project_id=project_id,
            branches=len(results),
            merged=len(merged),
        )
        return merged


def _project_doc_query(*, project_id: NotBlankStr, base: MemoryQuery) -> MemoryQuery:
    """Build the project-doc-scoped sibling query from *base*.

    Returns:
        A copy of ``base`` scoped to the ``PROJECT_DOC`` category, docs
        namespace, and the project tag.
    """
    project_tag = NotBlankStr(f"{DOCS_PROJECT_TAG_PREFIX}{project_id}")
    return base.model_copy(
        update={
            "categories": frozenset({MemoryCategory.PROJECT_DOC}),
            "namespaces": frozenset({DOCS_MEMORY_NAMESPACE}),
            "tags": (*base.tags, project_tag),
        }
    )


def _knowledge_query(*, scope_tag: NotBlankStr, base: MemoryQuery) -> MemoryQuery:
    """Build a knowledge-scoped sibling query (project or global) from *base*.

    Returns:
        A copy of ``base`` scoped to the ``KNOWLEDGE`` category, knowledge
        namespace, and the given ``scope_tag``.
    """
    return base.model_copy(
        update={
            "categories": frozenset({MemoryCategory.KNOWLEDGE}),
            "namespaces": frozenset({KNOWLEDGE_MEMORY_NAMESPACE}),
            "tags": (*base.tags, scope_tag),
        }
    )


def _merge_by_score(
    *result_sets: tuple[MemoryEntry, ...],
    limit: int,
) -> tuple[MemoryEntry, ...]:
    """Interleave entries by descending ``relevance_score`` and truncate.

    Returns:
        The combined entries sorted by descending relevance score and
        truncated to ``limit``.
    """
    combined = [entry for result in result_sets for entry in result]
    combined.sort(
        key=lambda entry: (
            entry.relevance_score if entry.relevance_score is not None else 0.0
        ),
        reverse=True,
    )
    return tuple(combined[:limit])
