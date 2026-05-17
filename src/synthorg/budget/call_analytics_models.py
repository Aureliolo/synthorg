"""Data models for call analytics aggregation results."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.category_analytics import OrchestrationRatio  # noqa: TC001


class AnalyticsAggregation(BaseModel):
    """Aggregated analytics over a set of cost records.

    Attributes:
        total_calls: Total number of LLM calls recorded.
        success_count: Calls with ``success=True``.
        failure_count: Calls with ``success=False``.
        retry_count: Calls that had at least one retry
            (``retry_count >= 1``).
        retry_rate: ``retry_count / total_calls``, or ``0.0`` when
            ``total_calls=0``.
        cache_hit_count: Calls with ``cache_hit=True``.
        cache_hit_rate: ``cache_hit_count / calls_with_cache_data``, or
            ``None`` when no records report cache hit status.
        avg_latency_ms: Mean latency over calls with latency data, or
            ``None`` when no records report latency.
        p95_latency_ms: 95th-percentile latency over calls with latency
            data, or ``None`` when no records report latency.
        orchestration_ratio: Token-based orchestration overhead ratio.
        by_finish_reason: Per finish-reason call counts as an immutable
            sorted tuple of ``(reason_str, count)`` pairs.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_calls: int = Field(ge=0, description="Total LLM calls recorded.")
    success_count: int = Field(ge=0, description="Calls with success=True.")
    failure_count: int = Field(ge=0, description="Calls with success=False.")
    retry_count: int = Field(ge=0, description="Calls with at least one retry.")
    retry_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of calls with at least one retry.",
    )
    cache_hit_count: int = Field(ge=0, description="Calls with cache_hit=True.")
    cache_hit_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of cache-reporting calls that were cache hits, or "
            "None when no records carry cache hit data."
        ),
    )
    avg_latency_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Mean latency in ms, or None when no latency data.",
    )
    p95_latency_ms: float | None = Field(
        default=None,
        ge=0.0,
        description=("95th-percentile latency in ms, or None when no latency data."),
    )
    orchestration_ratio: OrchestrationRatio = Field(
        description="Token-based orchestration overhead ratio.",
    )
    by_finish_reason: tuple[tuple[str, int], ...] = Field(
        description=(
            "Per finish-reason call counts, sorted alphabetically by reason string."
        ),
    )

    @model_validator(mode="after")
    def _validate_count_consistency(self) -> Self:
        """Enforce count invariants across aggregation fields."""
        if self.retry_count > self.total_calls:
            msg = "retry_count cannot exceed total_calls"
            raise ValueError(msg)
        if self.success_count + self.failure_count > self.total_calls:
            msg = "success_count + failure_count cannot exceed total_calls"
            raise ValueError(msg)
        return self
