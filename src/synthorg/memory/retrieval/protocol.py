"""Retrieval worker and hierarchical retriever protocols.

Defines the structural interfaces for pluggable retrieval workers
and the supervisor-worker hierarchical retriever.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.memory.retrieval.models import (
    FinalRetrievalResult,
    RetrievalQuery,
    RetrievalResult,
)


@runtime_checkable
class RetrievalWorker(Protocol):
    """One retrieval channel (semantic, episodic, procedural, etc.).

    Each worker searches a specific memory scope and returns scored
    candidates.  Workers are constructed with a ``MemoryBackend``
    reference and apply category-specific filtering internally.
    """

    @property
    def name(self) -> NotBlankStr:
        """Worker identifier (e.g. ``"semantic"``, ``"episodic"``)."""
        ...

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Execute retrieval for this worker's scope.

        Args:
            query: Unified retrieval query.

        Returns:
            Worker-scoped retrieval result with scored candidates.
        """
        ...


@runtime_checkable
class HierarchicalRetriever(Protocol):
    """Supervisor-worker retriever.

    Supervisor analyses the query, routes to workers, synthesizes
    results, and optionally retries on poor quality.
    """

    async def retrieve(
        self,
        query: RetrievalQuery,
    ) -> FinalRetrievalResult:
        """Execute the full hierarchical retrieval pipeline.

        Args:
            query: Unified retrieval query.

        Returns:
            Merged, deduplicated, and sorted retrieval result.
        """
        ...
