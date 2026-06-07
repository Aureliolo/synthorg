"""Retrieval-source and deduplicator protocols.

A :class:`RetrievalSource` fetches candidate items for one sub-query from a
single source family (knowledge / web / academic / code). A
:class:`Deduplicator` collapses near-duplicate candidates before synthesis.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.research.constants import RESEARCH_DEFAULT_PER_QUERY_LIMIT

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.research.enums import ResearchSourceType
    from synthorg.research.models import RetrievedItem, SubQuery


@runtime_checkable
class RetrievalSource(Protocol):
    """Fetches candidate sources for a single sub-query."""

    @property
    def source_type(self) -> ResearchSourceType:
        """The source family this implementation serves."""
        ...

    async def retrieve(
        self,
        sub_query: SubQuery,
        *,
        project_id: NotBlankStr | None = None,
        limit: int = RESEARCH_DEFAULT_PER_QUERY_LIMIT,
    ) -> tuple[RetrievedItem, ...]:
        """Return candidate items for *sub_query*, ranked by relevance.

        Implementations assign each item a stable ``ref_id`` of the form
        ``src-<sub_query.index>-<position>`` so references are globally
        unique within a run, and do not filter by credibility (that is the
        triage stage's job).
        """
        ...


@runtime_checkable
class Deduplicator(Protocol):
    """Collapses near-duplicate retrieved items."""

    async def dedupe(
        self,
        items: tuple[RetrievedItem, ...],
    ) -> tuple[RetrievedItem, ...]:
        """Return *items* with near-duplicates removed.

        The highest-relevance item in each duplicate cluster is kept and
        the original relative order of kept items is preserved.
        """
        ...
