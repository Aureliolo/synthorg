"""Academic retrieval source.

Wraps the vendor-agnostic
:class:`~synthorg.research.retrieval.providers.AcademicSearchProvider` (no
bundled implementation; injected at runtime), mapping each paper into a
:class:`RetrievedItem` with an :class:`AcademicSourceLocator` citation.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.research.constants import RESEARCH_DEFAULT_PER_QUERY_LIMIT
from synthorg.research.enums import ResearchSourceType
from synthorg.research.models import (
    AcademicSourceLocator,
    ResearchCitation,
    RetrievedItem,
    SubQuery,
)
from synthorg.research.retrieval.sources._shared import (
    make_ref_id,
    positional_relevance,
    truncate_snippet,
)
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from synthorg.research.retrieval.providers import AcademicSearchProvider


class AcademicRetrievalSource:
    """Retrieves academic papers through an injected search provider."""

    __slots__ = ("_provider",)

    def __init__(self, *, provider: AcademicSearchProvider) -> None:
        self._provider = provider

    @property
    def source_type(self) -> ResearchSourceType:
        """This source serves the academic family."""
        return ResearchSourceType.ACADEMIC

    async def retrieve(
        self,
        sub_query: SubQuery,
        *,
        project_id: NotBlankStr | None = None,  # noqa: ARG002 -- protocol contract
        limit: int = RESEARCH_DEFAULT_PER_QUERY_LIMIT,
    ) -> tuple[RetrievedItem, ...]:
        """Return ranked academic results as retrieved items."""
        results = await self._provider.search(sub_query.query_text, limit)
        items: list[RetrievedItem] = []
        for position, result in enumerate(results):
            ref_id = make_ref_id(sub_query.index, position)
            snippet = truncate_snippet(result.abstract)
            uri = (result.url or result.identifier or "").strip()
            if not uri:
                continue
            citation = ResearchCitation(
                ref_id=ref_id,
                source_type=ResearchSourceType.ACADEMIC,
                external=AcademicSourceLocator(
                    identifier=result.identifier,
                    doi=result.doi,
                    authors=result.authors,
                    year=result.year,
                ),
            )
            items.append(
                RetrievedItem(
                    ref_id=ref_id,
                    sub_query_index=sub_query.index,
                    source_type=ResearchSourceType.ACADEMIC,
                    title=result.title,
                    uri=NotBlankStr(uri),
                    snippet=snippet,
                    content_hash=compute_text_hash(snippet),
                    relevance_score=positional_relevance(position, len(results)),
                    citation=citation,
                )
            )
        return tuple(items)
