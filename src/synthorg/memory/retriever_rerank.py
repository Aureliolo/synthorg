# module-kind: code
"""Query-specific re-ranking of scored memories.

Split from ``retriever`` so the ``ScoredMemory`` <-> ``RetrievalCandidate``
marshalling around the re-ranker does not push the retriever over its
size budget. The re-ranker speaks the hierarchical-retrieval candidate
type; this adapts the flat pipeline's ``ScoredMemory`` to it and back.
"""

from synthorg.core.types import NotBlankStr
from synthorg.memory.ranking import ScoredMemory
from synthorg.memory.retrieval.models import RetrievalCandidate, RetrievalQuery
from synthorg.memory.retrieval.reranking.protocol import QuerySpecificReranker


async def apply_query_reranking(
    *,
    reranker: QuerySpecificReranker,
    query_text: NotBlankStr,
    agent_id: NotBlankStr,
    ranked: tuple[ScoredMemory, ...],
) -> tuple[ScoredMemory, ...]:
    """Re-rank scored memories against the query.

    Args:
        reranker: The wired re-ranker.
        query_text: The query string.
        agent_id: Owning agent for the retrieval query.
        ranked: Pre-rerank scored memories.

    Returns:
        The re-ranked memories.
    """
    query = RetrievalQuery(text=query_text, agent_id=agent_id)
    candidates = tuple(
        RetrievalCandidate(
            entry=s.entry,
            relevance_score=s.relevance_score,
            recency_score=s.recency_score,
            combined_score=s.combined_score,
            source_worker="flat",
            is_shared=s.is_shared,
        )
        for s in ranked
    )
    reranked = await reranker.rerank(query, candidates)
    return tuple(
        ScoredMemory(
            entry=c.entry,
            relevance_score=c.relevance_score,
            recency_score=c.recency_score,
            combined_score=c.combined_score,
            is_shared=c.is_shared,
        )
        for c in reranked
    )


__all__ = ["apply_query_reranking"]
