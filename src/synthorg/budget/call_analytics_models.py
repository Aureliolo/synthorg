# module-kind: code
"""Data models for call analytics aggregation results."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.category_analytics import OrchestrationRatio
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.model_tier import TierName
from synthorg.core.types import NotBlankStr


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
        """Enforce count invariants across aggregation fields.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.retry_count > self.total_calls:
            msg = "retry_count cannot exceed total_calls"
            raise ValueError(msg)
        if self.success_count + self.failure_count > self.total_calls:
            msg = "success_count + failure_count cannot exceed total_calls"
            raise ValueError(msg)
        return self


class PromptClassBreakdownRow(BaseModel):
    """Cost + latency + quality metrics for one prompt class.

    One row aggregates every cost record tagged with a single
    ``prompt_class_id`` (a registered ``PromptPurposeId``), so the operator
    dashboard can slice spend, latency, and reliability by prompt purpose.

    Attributes:
        prompt_class_id: The ``PromptPurposeId`` value the records carry.
        tier: The design tier the purpose is pinned to, or ``None`` when the
            id is not in the tier policy.
        total_cost: Sum of cost across the class's records.
        currency: ISO 4217 code shared by the class's records.
        call_count: Number of records for this class.
        input_tokens: Sum of input tokens.
        output_tokens: Sum of output tokens.
        avg_latency_ms: Mean latency over latency-reporting calls, or ``None``.
        p95_latency_ms: 95th-percentile latency, or ``None``.
        cache_hit_rate: Fraction of cache-reporting calls that hit, or ``None``.
        retry_rate: Fraction of calls with at least one retry.
        success_rate: Fraction of success-reporting calls that succeeded, or
            ``None`` when no record carries success data.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    prompt_class_id: NotBlankStr = Field(description="Registered prompt purpose id.")
    tier: TierName | None = Field(
        default=None,
        description="Design tier the purpose is pinned to, or None.",
    )
    total_cost: float = Field(ge=0.0, description="Total cost for the class.")
    currency: str = Field(
        default=DEFAULT_CURRENCY,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code.",
    )
    call_count: int = Field(
        gt=0, description="Records for this class (a row aggregates at least one)."
    )
    input_tokens: int = Field(ge=0, description="Total input tokens.")
    output_tokens: int = Field(ge=0, description="Total output tokens.")
    avg_latency_ms: float | None = Field(
        default=None, ge=0.0, description="Mean latency in ms, or None."
    )
    p95_latency_ms: float | None = Field(
        default=None, ge=0.0, description="95th-percentile latency in ms, or None."
    )
    cache_hit_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Cache-hit fraction over cache-reporting calls, or None.",
    )
    retry_rate: float = Field(
        ge=0.0, le=1.0, description="Fraction of calls with at least one retry."
    )
    success_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Success fraction over success-reporting calls, or None.",
    )


class PromptClassBreakdown(BaseModel):
    """Per-prompt-class cost + latency breakdown across cost records.

    Attributes:
        rows: One :class:`PromptClassBreakdownRow` per prompt class with at
            least one matching record, sorted by ``prompt_class_id``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    rows: tuple[PromptClassBreakdownRow, ...] = Field(
        default=(),
        description="Per-prompt-class rows, sorted by prompt_class_id.",
    )

    @model_validator(mode="after")
    def _rows_sorted_and_unique(self) -> Self:
        """Enforce the rows contract: one row per class, sorted by id.

        The dashboard renders rows directly, so out-of-order or duplicate
        ``prompt_class_id`` rows would mis-display silently.

        Returns:
            The validated breakdown.

        Raises:
            ValueError: If rows are not strictly sorted by ``prompt_class_id``.
        """
        ids = [row.prompt_class_id for row in self.rows]
        if ids != sorted(ids):
            msg = "PromptClassBreakdown rows must be sorted by prompt_class_id"
            raise ValueError(msg)
        if len(ids) != len(set(ids)):
            msg = "PromptClassBreakdown rows must have a unique prompt_class_id"
            raise ValueError(msg)
        return self
