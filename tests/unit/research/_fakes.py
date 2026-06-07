"""Test doubles for research-subsystem unit tests.

Structural fakes for the vendor-agnostic search providers, plus small
builders for completion responses and knowledge hits used to drive the
LLM-backed and knowledge-backed strategies deterministically.
"""

from synthorg.knowledge.enums import SourceType
from synthorg.knowledge.models import Citation, KnowledgeHit, WebLocator
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.research_protocol import ResearchRunFilter
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.research.models import ResearchRun
from synthorg.research.retrieval.providers import AcademicResult, CodeResult
from synthorg.tools.web.web_search import SearchResult

_HASH = "b" * 64


def scripted_response(content: str, *, cost: float = 0.01) -> CompletionResponse:
    """Build a completion response carrying *content* and a token cost."""
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=10, cost=cost),
        model="example-medium-001",
    )


def knowledge_hit(
    *,
    chunk_id: str,
    text: str,
    score: float = 0.8,
    title: str = "Internal doc",
    uri: str = "doc://internal/1",
) -> KnowledgeHit:
    """Build a cited knowledge hit for the knowledge-source tests."""
    citation = Citation(
        source_id="ks-1",
        chunk_id=chunk_id,
        source_type=SourceType.WEB,
        title=title,
        uri=uri,
        locator=WebLocator(url=uri, char_start=0, char_end=len(text)),
        content_hash=_HASH,
    )
    return KnowledgeHit(chunk_text=text, relevance_score=score, citation=citation)


class FakeWebSearchProvider:
    """Structural ``WebSearchProvider`` returning preset results."""

    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results
        self.queries: list[str] = []

    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        """Record the query and return the preset results, capped."""
        self.queries.append(query)
        return self._results[:max_results]


class FakeAcademicSearchProvider:
    """Structural ``AcademicSearchProvider`` returning preset results."""

    def __init__(self, results: list[AcademicResult]) -> None:
        self._results = results

    async def search(self, query: str, max_results: int = 10) -> list[AcademicResult]:
        """Return the preset academic results, capped."""
        del query
        return self._results[:max_results]


class FakeCodeSearchProvider:
    """Structural ``CodeSearchProvider`` returning preset results."""

    def __init__(self, results: list[CodeResult]) -> None:
        self._results = results

    async def search(self, query: str, max_results: int = 10) -> list[CodeResult]:
        """Return the preset code results, capped."""
        del query
        return self._results[:max_results]


class InMemoryResearchRunRepository:
    """Structural ``ResearchRunRepository`` backed by a dict."""

    def __init__(self) -> None:
        self._rows: dict[str, ResearchRun] = {}

    async def save(self, entity: ResearchRun) -> None:
        """Upsert a run keyed by ``run_id``."""
        self._rows[entity.run_id] = entity

    async def get(self, entity_id: str) -> ResearchRun | None:
        """Return a run by id, or None."""
        return self._rows.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        """Delete a run by id; True iff it existed."""
        return self._rows.pop(entity_id, None) is not None

    def _ordered(self) -> list[ResearchRun]:
        return sorted(
            self._rows.values(),
            key=lambda r: (r.created_at, r.run_id),
            reverse=True,
        )

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ResearchRun, ...]:
        """List runs, most-recent first."""
        return tuple(self._ordered()[offset : offset + limit])

    def _matches(self, run: ResearchRun, spec: ResearchRunFilter) -> bool:
        if spec.brief_id is not None and run.brief_id != spec.brief_id:
            return False
        if spec.project_id is not None and run.project_id != spec.project_id:
            return False
        return not (spec.status is not None and run.status is not spec.status)

    async def query(
        self,
        filter_spec: ResearchRunFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ResearchRun, ...]:
        """Return runs matching the filter, most-recent first."""
        matched = [r for r in self._ordered() if self._matches(r, filter_spec)]
        return tuple(matched[offset : offset + limit])

    async def count(self, filter_spec: ResearchRunFilter) -> int:
        """Count runs matching the filter."""
        return sum(1 for r in self._rows.values() if self._matches(r, filter_spec))
