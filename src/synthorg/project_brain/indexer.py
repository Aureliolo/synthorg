"""RAG indexer for project-brain entries.

Writes :class:`BrainChunk` rows to the memory backend under
:attr:`MemoryCategory.PROJECT_BRAIN` with namespace
:data:`BRAIN_MEMORY_NAMESPACE`. Before storing fresh chunks for an entry it
deletes any prior chunks tagged with the entry's ``brain_entry:<id>`` tag, so a
re-index is idempotent (no duplicates accumulate as revisions append).

The indexer is the only brain component that holds a reference to the memory
backend; the chunker is pure and the writer talks to the workspace.
"""

import asyncio
import builtins
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryMetadata, MemoryQuery, MemoryStoreRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.project_brain import (
    BRAIN_ENTRY_INDEX_FAILED,
    BRAIN_ENTRY_INDEXED,
)
from synthorg.project_brain.constants import (
    BRAIN_ENTRY_TAG_PREFIX,
    BRAIN_MEMORY_NAMESPACE,
    BRAIN_PROJECT_TAG_PREFIX,
    SYSTEM_BRAIN_AGENT_ID,
)
from synthorg.project_brain.errors import BrainIndexError

if TYPE_CHECKING:
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.project_brain.models import BrainChunk

logger = get_logger(__name__)

_INDEX_PAGE_SIZE: int = 100
_MAX_DELETE_ITERATIONS: int = 100


class BrainIndexer:
    """Stores :class:`BrainChunk` rows into the agent memory backend."""

    __slots__ = ("_backend",)

    def __init__(self, *, backend: MemoryBackend) -> None:
        self._backend = backend

    async def index(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        chunks: tuple[BrainChunk, ...],
    ) -> None:
        """Replace any prior chunks for *(project_id, entry_id)* with *chunks*.

        Args:
            project_id: Owning project.
            entry_id: Logical entry id.
            chunks: New chunk set to store.

        Raises:
            BrainIndexError: If the backend rejects either the delete-prior
                phase or any store call.
        """
        try:
            await self._delete_prior(project_id=project_id, entry_id=entry_id)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                BRAIN_ENTRY_INDEX_FAILED,
                project_id=project_id,
                entry_id=entry_id,
                phase="delete_prior",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"Failed to clear prior chunks for {project_id!r}/{entry_id!r} "
                f"before re-index"
            )
            raise BrainIndexError(msg) from exc
        try:
            async with asyncio.TaskGroup() as tg:
                for chunk in chunks:
                    _ = tg.create_task(
                        self._backend.store(
                            SYSTEM_BRAIN_AGENT_ID,
                            _chunk_to_request(chunk),
                        )
                    )
        except builtins.BaseExceptionGroup as group:
            if group.subgroup(asyncio.CancelledError) is not None:
                raise
            if group.subgroup((MemoryError, RecursionError)) is not None:
                raise
            cause = group.exceptions[0]
            logger.warning(
                BRAIN_ENTRY_INDEX_FAILED,
                project_id=project_id,
                entry_id=entry_id,
                phase="store",
                error_type=type(cause).__name__,
                error=safe_error_description(cause),
            )
            msg = f"Failed to store chunks for {project_id!r}/{entry_id!r}"
            raise BrainIndexError(msg) from cause
        logger.info(
            BRAIN_ENTRY_INDEXED,
            project_id=project_id,
            entry_id=entry_id,
            chunk_count=len(chunks),
        )

    async def _delete_prior(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
    ) -> None:
        """Delete all PROJECT_BRAIN entries tagged with this entry id.

        Paginates through ``backend.retrieve`` with the entry tag filter and
        issues a ``delete(...)`` for each hit. Idempotent: running on a
        brand-new entry id is a no-op.

        Raises:
            BrainIndexError: When deletion does not converge within
                ``_MAX_DELETE_ITERATIONS`` pages (backend not removing entries).
        """
        project_tag = NotBlankStr(f"{BRAIN_PROJECT_TAG_PREFIX}{project_id}")
        entry_tag = NotBlankStr(f"{BRAIN_ENTRY_TAG_PREFIX}{entry_id}")
        for _ in range(_MAX_DELETE_ITERATIONS):
            existing = await self._backend.retrieve(
                SYSTEM_BRAIN_AGENT_ID,
                MemoryQuery(
                    text=None,
                    categories=frozenset({MemoryCategory.PROJECT_BRAIN}),
                    namespaces=frozenset({BRAIN_MEMORY_NAMESPACE}),
                    tags=(project_tag, entry_tag),
                    limit=_INDEX_PAGE_SIZE,
                ),
            )
            if not existing:
                return
            for entry in existing:
                await self._backend.delete(SYSTEM_BRAIN_AGENT_ID, entry.id)
        msg = (
            f"delete-prior for {project_id!r}/{entry_id!r} did not converge after "
            f"{_MAX_DELETE_ITERATIONS} pages (backend not removing entries?)"
        )
        raise BrainIndexError(msg)


def _chunk_to_request(chunk: BrainChunk) -> MemoryStoreRequest:
    """Translate a :class:`BrainChunk` to a :class:`MemoryStoreRequest`.

    Returns:
        A ``MemoryStoreRequest`` carrying the chunk text under the
        ``PROJECT_BRAIN`` category with the chunk's tags.
    """
    return MemoryStoreRequest(
        category=MemoryCategory.PROJECT_BRAIN,
        namespace=BRAIN_MEMORY_NAMESPACE,
        content=chunk.text,
        metadata=MemoryMetadata(
            source=NotBlankStr("project_brain.indexer"),
            tags=chunk.tags,
        ),
    )
