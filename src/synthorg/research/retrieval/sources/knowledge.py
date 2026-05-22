"""Knowledge-substrate retrieval source.

Wraps :class:`~synthorg.knowledge.service.KnowledgeService`, mapping each
cited :class:`~synthorg.knowledge.models.KnowledgeHit` into a
:class:`RetrievedItem` whose citation embeds the resolved knowledge
:class:`~synthorg.knowledge.models.Citation`.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import ResearchSourceType
from synthorg.core.types import NotBlankStr
from synthorg.research.constants import RESEARCH_DEFAULT_PER_QUERY_LIMIT
from synthorg.research.models import ResearchCitation, RetrievedItem
from synthorg.research.retrieval.sources._shared import make_ref_id, truncate_snippet
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from synthorg.knowledge.service import KnowledgeService
    from synthorg.research.models import SubQuery


class KnowledgeRetrievalSource:
    """Retrieves cited hits from the internal knowledge substrate."""

    __slots__ = ("_service",)

    def __init__(self, *, service: KnowledgeService) -> None:
        self._service = service

    @property
    def source_type(self) -> ResearchSourceType:
        """This source serves the knowledge family."""
        return ResearchSourceType.KNOWLEDGE

    async def retrieve(
        self,
        sub_query: SubQuery,
        *,
        project_id: NotBlankStr | None = None,
        limit: int = RESEARCH_DEFAULT_PER_QUERY_LIMIT,
    ) -> tuple[RetrievedItem, ...]:
        """Return cited knowledge hits as retrieved items."""
        hits = await self._service.search(
            query=NotBlankStr(sub_query.query_text),
            project_id=project_id,
            limit=limit,
        )
        items: list[RetrievedItem] = []
        for position, hit in enumerate(hits):
            ref_id = make_ref_id(sub_query.index, position)
            snippet = truncate_snippet(hit.chunk_text)
            citation = ResearchCitation(
                ref_id=ref_id,
                source_type=ResearchSourceType.KNOWLEDGE,
                knowledge=hit.citation,
            )
            items.append(
                RetrievedItem(
                    ref_id=ref_id,
                    sub_query_index=sub_query.index,
                    source_type=ResearchSourceType.KNOWLEDGE,
                    title=hit.citation.title,
                    uri=hit.citation.uri,
                    snippet=snippet,
                    content_hash=compute_text_hash(snippet),
                    relevance_score=hit.relevance_score,
                    citation=citation,
                )
            )
        return tuple(items)
