"""Test doubles for research-subsystem unit tests.

Structural fakes for the vendor-agnostic search providers, plus small
builders for completion responses and knowledge hits used to drive the
LLM-backed and knowledge-backed strategies deterministically.
"""

from synthorg.core.enums import SourceType
from synthorg.knowledge.models import Citation, KnowledgeHit, WebLocator
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage
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
