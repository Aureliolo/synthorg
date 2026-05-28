"""Query-specific re-ranker protocol.

Defines the structural interface for post-fusion re-ranking of
retrieval candidates using query-specific scoring.
"""

from typing import Protocol, runtime_checkable

from synthorg.memory.retrieval.models import (
    RetrievalCandidate,
    RetrievalQuery,
)


@runtime_checkable
class QuerySpecificReranker(Protocol):
    """Post-fusion re-ranker using query-specific scoring.

    Takes the top-K fused candidates and scores each against the
    current query using an LLM or other scoring function.  The
    contract is ordering-only: implementations MUST return the same
    candidate set in a new sequence and SHOULD preserve the original
    ``combined_score`` values so downstream filtering sees
    consistent scores.  The LLM preference signal is encoded solely
    in the output order.
    """

    async def rerank(
        self,
        query: RetrievalQuery,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RetrievalCandidate, ...]:
        """Re-rank candidates using query-specific criteria.

        Args:
            query: The original retrieval query.
            candidates: Post-fusion candidates to re-rank.

        Returns:
            Same candidates in a new sequence order.  Original
            ``combined_score`` values are preserved -- the rerank
            signal is encoded only in the output ordering.
        """
        ...
