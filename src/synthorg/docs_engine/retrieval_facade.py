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
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_MEMORY_NAMESPACE,
    DOCS_PROJECT_TAG_PREFIX,
    SYSTEM_DOCS_AGENT_ID,
)
from synthorg.engine.prompt_safety import TAG_BRAIN_STATE, wrap_untrusted
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
from synthorg.project_brain.constants import (
    BRAIN_MEMORY_NAMESPACE,
    BRAIN_PROJECT_TAG_PREFIX,
    SYSTEM_BRAIN_AGENT_ID,
)

if TYPE_CHECKING:
    from synthorg.memory.models import MemoryEntry, MemoryQuery
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)


class ProjectAwareMemoryFacade:
    """Merges agent + project-doc (+ knowledge + brain) retrieval results."""

    __slots__ = ("_backend", "_brain_enabled", "_knowledge_enabled")

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        knowledge_enabled: bool = False,
        brain_enabled: bool = False,
    ) -> None:
        self._backend = backend
        self._knowledge_enabled = knowledge_enabled
        self._brain_enabled = brain_enabled

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
        if self._brain_enabled:
            targets.append(
                (
                    SYSTEM_BRAIN_AGENT_ID,
                    _brain_query(project_id=project_id, base=query),
                )
            )
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._safe_retrieve(target_id, target_query))
                for target_id, target_query in targets
            ]
        # Keep every leg that succeeded so one backend failure does not discard
        # the other legs' hits. ``_safe_retrieve`` returns the exception for an
        # ordinary per-leg failure -- so the TaskGroup never cancels the sibling
        # legs -- and re-raises interpreter-critical errors, which propagate out
        # of the group as a ``BaseExceptionGroup``.
        results: list[tuple[MemoryEntry, ...]] = []
        failures: list[BaseException] = []
        for task in tasks:
            outcome = task.result()
            if isinstance(outcome, BaseException):
                failures.append(outcome)
            else:
                results.append(outcome)
        if failures:
            logger.warning(
                DOC_FACADE_FANOUT_FAILED,
                agent_id=agent_id,
                project_id=project_id,
                exceptions=tuple(type(exc).__name__ for exc in failures),
                error=safe_error_description(failures[0]),
            )
        if not results:
            # Every leg failed; fall back to an agent-only retrieve so a total
            # fan-out failure still returns the agent's own memories.
            return await self._backend.retrieve(agent_id, query)
        merged = _merge_by_score(*results, limit=query.limit)
        wrapped = tuple(_wrap_brain_state(entry) for entry in merged)
        logger.debug(
            DOC_FACADE_FANOUT,
            agent_id=agent_id,
            project_id=project_id,
            branches=len(results),
            merged=len(wrapped),
        )
        return wrapped

    async def _safe_retrieve(
        self, agent_id: NotBlankStr, query: MemoryQuery
    ) -> tuple[MemoryEntry, ...] | BaseException:
        """Retrieve one fan-out leg, returning (not raising) ordinary failures.

        Wrapping each leg this way keeps the :class:`asyncio.TaskGroup` from
        cancelling the sibling legs when one backend call fails: an ordinary
        exception is captured and returned so the caller can log and drop it,
        while interpreter-critical errors (``MemoryError`` / ``RecursionError``)
        and ``KeyboardInterrupt`` / ``SystemExit`` are re-raised and propagate
        out of the group. A ``CancelledError`` is a ``BaseException`` (not
        ``Exception``), so it also propagates rather than being swallowed.

        Returns:
            The leg's entries on success, or the exception it raised when that
            failure is an ordinary, non-critical one.
        """
        try:
            return await self._backend.retrieve(agent_id, query)
        except Exception as exc:
            reraise_critical(exc)
            return exc


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


def _brain_query(*, project_id: NotBlankStr, base: MemoryQuery) -> MemoryQuery:
    """Build the project-brain-scoped sibling query from *base*.

    Returns:
        A copy of ``base`` scoped to the ``PROJECT_BRAIN`` category, brain
        namespace, and the project tag.
    """
    project_tag = NotBlankStr(f"{BRAIN_PROJECT_TAG_PREFIX}{project_id}")
    return base.model_copy(
        update={
            "categories": frozenset({MemoryCategory.PROJECT_BRAIN}),
            "namespaces": frozenset({BRAIN_MEMORY_NAMESPACE}),
            "tags": (*base.tags, project_tag),
        }
    )


def _wrap_brain_state(entry: MemoryEntry) -> MemoryEntry:
    """Fence a project-brain entry's content under ``TAG_BRAIN_STATE``.

    Brain entries are authored by agents and the operator, so on re-entry they
    are attacker-controllable; wrapping the content at this retrieval boundary
    (never on storage) keeps the resuming agent from following instructions an
    upstream writer may have embedded. Entries of any other category pass
    through unchanged.

    Returns:
        The entry with its content fenced when it is a ``PROJECT_BRAIN`` entry,
        otherwise the entry unchanged.
    """
    if entry.category is not MemoryCategory.PROJECT_BRAIN:
        return entry
    return entry.model_copy(
        update={"content": wrap_untrusted(TAG_BRAIN_STATE, entry.content)}
    )


def _merge_by_score(
    *result_sets: tuple[MemoryEntry, ...],
    limit: int,
) -> tuple[MemoryEntry, ...]:
    """Combine entries, sort by descending ``relevance_score``, and truncate.

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
