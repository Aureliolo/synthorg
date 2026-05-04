"""Retrieval pipeline data models.

Unified input/output models for both flat and hierarchical retrieval
pipelines.  All models are frozen Pydantic with ``allow_inf_nan=False``.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import MemoryCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.models import MemoryEntry  # noqa: TC001


class RetrievalQuery(BaseModel):
    """Unified query input for both flat and hierarchical retrievers.

    Attributes:
        text: Semantic search text.
        agent_id: Agent context for supervisor routing decisions.
        categories: Optional category filter (``None`` = all).
        max_results: Maximum candidates to return.
        token_budget: Optional token limit for result formatting.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    text: NotBlankStr = Field(description="Semantic search text")
    agent_id: NotBlankStr = Field(
        description="Agent context for supervisor routing",
    )
    categories: frozenset[MemoryCategory] | None = Field(
        default=None,
        description="Optional category filter (None = all)",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum candidates to return",
    )
    token_budget: int | None = Field(
        default=None,
        ge=1,
        description="Optional token limit for result formatting",
    )


class RetrievalCandidate(BaseModel):
    """Single scored result with provenance metadata.

    Attributes:
        entry: The underlying memory entry.
        relevance_score: Backend relevance score (0.0-1.0).
        recency_score: Recency decay score (0.0-1.0).
        combined_score: Final ranking signal (0.0-1.0).
        source_worker: Which worker produced this candidate.
        is_shared: Whether from SharedKnowledgeStore.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry: MemoryEntry = Field(description="The underlying memory entry")
    relevance_score: float = Field(
        ge=0.0,
        description="Backend relevance score (may exceed 1.0 with boosts)",
    )
    recency_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Recency decay score",
    )
    combined_score: float = Field(
        ge=0.0,
        description="Final ranking signal (may exceed 1.0 with boosts)",
    )
    source_worker: NotBlankStr = Field(
        description="Which worker produced this candidate",
    )
    is_shared: bool = Field(
        default=False,
        description="Whether from SharedKnowledgeStore",
    )


class RetrievalResult(BaseModel):
    """Single worker's retrieval output.

    Attributes:
        candidates: Results from this worker.
        worker_name: Worker identifier (e.g. ``"semantic"``).
        execution_ms: Execution time in milliseconds.
        error: Error message if retrieval failed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    candidates: tuple[RetrievalCandidate, ...] = Field(
        default=(),
        description="Results from this worker",
    )
    worker_name: NotBlankStr = Field(
        description="Worker identifier",
    )
    execution_ms: int = Field(
        default=0,
        ge=0,
        description="Execution time in milliseconds",
    )
    error: NotBlankStr | None = Field(
        default=None,
        description=(
            "Error description produced by `safe_error_description(exc)` "
            "(`from synthorg.observability import safe_error_description`); "
            "typically `{ExceptionType}: {scrubbed_message}`, or just "
            "`{ExceptionType}` when the exception message is empty. "
            "`None` on success. Always populate via the helper, never via "
            "raw `str(exc)`, so credential material in exception messages "
            "is scrubbed before the value crosses any persistence / API / "
            "RPC boundary."
        ),
    )


class FinalRetrievalResult(BaseModel):
    """Merged output from all workers after deduplication and sorting.

    Attributes:
        candidates: Deduplicated and sorted candidates.
        worker_results: Per-worker outputs for observability.
        retries_performed: Number of reflective retry attempts.
        rerank_applied: Whether query-specific re-ranking was applied.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    candidates: tuple[RetrievalCandidate, ...] = Field(
        default=(),
        description="Deduplicated and sorted candidates",
    )
    worker_results: tuple[RetrievalResult, ...] = Field(
        default=(),
        description="Per-worker outputs for observability",
    )
    retries_performed: int = Field(
        default=0,
        ge=0,
        description="Number of reflective retry attempts",
    )
    rerank_applied: bool = Field(
        default=False,
        description="Whether query-specific re-ranking was applied",
    )
