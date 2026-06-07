"""Web retrieval source.

Wraps the vendor-agnostic
:class:`~synthorg.tools.web.web_search.WebSearchProvider` (no bundled
implementation; injected at runtime), mapping each result into a
:class:`RetrievedItem` with a :class:`WebSourceLocator` citation. Snippets
are untrusted and are wrapped only where they enter a prompt (in synthesis).
"""

from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.research.constants import RESEARCH_DEFAULT_PER_QUERY_LIMIT
from synthorg.research.enums import ResearchSourceType
from synthorg.research.models import (
    ResearchCitation,
    RetrievedItem,
    SubQuery,
    WebSourceLocator,
)
from synthorg.research.retrieval.sources._shared import (
    make_ref_id,
    positional_relevance,
    truncate_snippet,
)
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from synthorg.tools.web.web_search import WebSearchProvider


class WebRetrievalSource:
    """Retrieves web results through an injected search provider."""

    __slots__ = ("_clock", "_provider")

    def __init__(
        self,
        *,
        provider: WebSearchProvider,
        clock: Clock | None = None,
    ) -> None:
        self._provider = provider
        self._clock = clock if clock is not None else SystemClock()

    @property
    def source_type(self) -> ResearchSourceType:
        """This source serves the web family."""
        return ResearchSourceType.WEB

    async def retrieve(
        self,
        sub_query: SubQuery,
        *,
        project_id: NotBlankStr | None = None,  # noqa: ARG002 -- protocol contract
        limit: int = RESEARCH_DEFAULT_PER_QUERY_LIMIT,
    ) -> tuple[RetrievedItem, ...]:
        """Return ranked web results as retrieved items."""
        results = await self._provider.search(sub_query.query_text, limit)
        accessed_at = self._clock.now()
        items: list[RetrievedItem] = []
        for position, result in enumerate(results):
            url = result.url.strip()
            if not url:
                continue
            ref_id = make_ref_id(sub_query.index, position)
            snippet = truncate_snippet(result.snippet)
            citation = ResearchCitation(
                ref_id=ref_id,
                source_type=ResearchSourceType.WEB,
                external=WebSourceLocator(
                    url=NotBlankStr(url),
                    accessed_at=accessed_at,
                ),
            )
            items.append(
                RetrievedItem(
                    ref_id=ref_id,
                    sub_query_index=sub_query.index,
                    source_type=ResearchSourceType.WEB,
                    title=result.title.strip() or url,
                    uri=NotBlankStr(url),
                    snippet=snippet,
                    content_hash=compute_text_hash(snippet),
                    relevance_score=positional_relevance(position, len(results)),
                    citation=citation,
                )
            )
        return tuple(items)
