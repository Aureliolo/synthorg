"""RAG indexer for living documents.

Writes :class:`DocChunk` rows to the memory backend under
:attr:`MemoryCategory.PROJECT_DOC` with namespace
:data:`DOCS_MEMORY_NAMESPACE`. Before storing fresh chunks for a doc,
deletes any prior chunks for the same ``(project_id, slug)`` so a
re-index is idempotent (no duplicates accumulate).

The indexer is the only component that holds a reference to the
backend across the docs engine; the chunker is pure and the writer
talks to the workspace, not the memory backend.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_MEMORY_NAMESPACE,
    DOCS_PROJECT_TAG_PREFIX,
    DOCS_SLUG_TAG_PREFIX,
    SYSTEM_DOCS_AGENT_ID,
)
from synthorg.docs_engine.errors import DocIndexError
from synthorg.memory.models import MemoryMetadata, MemoryQuery, MemoryStoreRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docs import (
    DOC_INDEX_FAILED,
    DOC_INDEXED,
)

if TYPE_CHECKING:
    from synthorg.docs_engine.models import DocChunk
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)

_INDEX_PAGE_SIZE: int = 100


class DocIndexer:
    """Stores :class:`DocChunk` rows into the agent memory backend."""

    __slots__ = ("_backend",)

    def __init__(self, *, backend: MemoryBackend) -> None:
        self._backend = backend

    async def index(
        self,
        *,
        project_id: NotBlankStr,
        slug: NotBlankStr,
        chunks: tuple[DocChunk, ...],
    ) -> None:
        """Replace any prior chunks for *(project_id, slug)* with *chunks*.

        Args:
            project_id: Owning project.
            slug: Doc slug.
            chunks: New chunk set to store.

        Raises:
            DocIndexError: If the backend rejects either the delete-prior
                phase or any store call.
        """
        try:
            await self._delete_prior(project_id=project_id, slug=slug)
        except Exception as exc:
            msg = (
                f"Failed to clear prior chunks for {project_id!r}/{slug!r} "
                f"before re-index"
            )
            logger.warning(
                DOC_INDEX_FAILED,
                project_id=project_id,
                slug=slug,
                phase="delete_prior",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DocIndexError(msg) from exc
        try:
            for chunk in chunks:
                await self._backend.store(
                    SYSTEM_DOCS_AGENT_ID,
                    _chunk_to_request(chunk),
                )
        except Exception as exc:
            msg = f"Failed to store chunks for {project_id!r}/{slug!r}"
            logger.warning(
                DOC_INDEX_FAILED,
                project_id=project_id,
                slug=slug,
                phase="store",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DocIndexError(msg) from exc
        logger.info(
            DOC_INDEXED,
            project_id=project_id,
            slug=slug,
            chunk_count=len(chunks),
        )

    async def _delete_prior(
        self,
        *,
        project_id: NotBlankStr,
        slug: NotBlankStr,
    ) -> None:
        """Delete all PROJECT_DOC entries tagged with the doc's slug.

        Paginates through ``backend.retrieve`` with the slug tag filter
        and issues a ``delete(...)`` for each hit. Idempotent: running
        on a brand-new slug is a no-op.
        """
        project_tag = NotBlankStr(f"{DOCS_PROJECT_TAG_PREFIX}{project_id}")
        slug_tag = NotBlankStr(f"{DOCS_SLUG_TAG_PREFIX}{slug}")
        # lint-allow: long-running-loop-kill-switch -- bounded drain
        while True:
            existing = await self._backend.retrieve(
                SYSTEM_DOCS_AGENT_ID,
                MemoryQuery(
                    text=None,
                    categories=frozenset({MemoryCategory.PROJECT_DOC}),
                    namespaces=frozenset({DOCS_MEMORY_NAMESPACE}),
                    tags=(project_tag, slug_tag),
                    limit=_INDEX_PAGE_SIZE,
                ),
            )
            if not existing:
                return
            for entry in existing:
                await self._backend.delete(SYSTEM_DOCS_AGENT_ID, entry.id)


def _chunk_to_request(chunk: DocChunk) -> MemoryStoreRequest:
    """Translate a :class:`DocChunk` to a :class:`MemoryStoreRequest`."""
    return MemoryStoreRequest(
        category=MemoryCategory.PROJECT_DOC,
        namespace=DOCS_MEMORY_NAMESPACE,
        content=chunk.text,
        metadata=MemoryMetadata(
            source=NotBlankStr("docs_engine.indexer"),
            tags=chunk.tags,
        ),
    )
