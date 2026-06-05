"""Per-turn execution record and trajectory enums.

``TurnRecord`` is the per-turn metadata an execution loop emits; the
``NodeType`` and ``BehaviorTag`` enums classify what happened in a turn.
These are pure data shapes consumed across subsystems (coordination
metrics, memory distillation, analytics), so they live in the
dependency-free ``synthorg.execution`` leaf rather than in
``engine.loop_protocol`` (whose package init pulls the whole engine).
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.types import NotBlankStr
from synthorg.execution.efficiency import EfficiencyRatios
from synthorg.providers.enums import FinishReason


class NodeType(StrEnum):
    """Type of computation node executed within a turn.

    Used for structural credit assignment and post-hoc trace analysis.
    Each turn records which node types executed, enabling fine-grained
    attribution of costs and failures.
    """

    LLM_CALL = "llm_call"
    TOOL_INVOCATION = "tool_invocation"
    QUALITY_CHECK = "quality_check"
    BUDGET_CHECK = "budget_check"
    STAGNATION_CHECK = "stagnation_check"


class BehaviorTag(StrEnum):
    """Behavior category for trace capture and eval routing.

    Starting taxonomy derived from agent evaluation patterns.
    Extend as usage patterns reveal category fragmentation or
    generalization.
    """

    FILE_OPERATIONS = "file_operations"
    RETRIEVAL = "retrieval"
    TOOL_USE = "tool_use"
    MEMORY = "memory"
    CONVERSATION = "conversation"
    SUMMARIZATION = "summarization"
    DELEGATION = "delegation"
    COORDINATION = "coordination"
    VERIFICATION = "verification"


class TurnRecord(BaseModel):
    """Per-turn metadata recorded during execution.

    Attributes:
        turn_number: 1-indexed turn number.
        input_tokens: Input tokens consumed this turn.
        output_tokens: Output tokens generated this turn.
        total_tokens: Sum of input and output tokens (computed).
        cost: Cost in the configured currency for this turn.
        tool_calls_made: Names of tools invoked this turn.
        tool_call_fingerprints: Deterministic fingerprints of tool
            calls (``name:args_hash``) for stagnation detection.
        finish_reason: LLM finish reason for this turn.
        call_category: Optional LLM call category for coordination
            metrics (productive, coordination, system).
        latency_ms: Round-trip latency in milliseconds (``None`` if not measured).
        cache_hit: Whether the provider served this turn from cache.
        retry_count: Number of retry attempts before success.
        retry_reason: Exception type name of the last retried error.
        node_types: Node types that executed in this turn (e.g.
            LLM_CALL, TOOL_INVOCATION). Defaults to empty for
            deserialization of legacy data.
        behavior_tags: Behavior categories inferred by BehaviorTaggerMiddleware.
        efficiency_delta: Efficiency ratios against an ideal baseline.
        prior_tool_call_count: Cumulative tool calls before this turn (for PTE).
        tool_response_tokens: Tokens from tool responses this turn (for PTE).
        semantic_drift_score: Similarity score (0.0--1.0) from
            SemanticDriftDetector, or ``None`` if not measured.
        success: Whether this turn completed without error or content filter (computed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    turn_number: int = Field(gt=0, description="1-indexed turn number")
    input_tokens: int = Field(ge=0, description="Input tokens this turn")
    output_tokens: int = Field(ge=0, description="Output tokens this turn")
    cost: float = Field(ge=0.0, description="Cost in the configured currency this turn")
    tool_calls_made: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tool names invoked this turn",
    )
    tool_call_fingerprints: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Deterministic fingerprints of tool calls (name:args_hash)",
    )
    finish_reason: FinishReason = Field(
        description="LLM finish reason this turn",
    )
    call_category: LLMCallCategory | None = Field(
        default=None,
        description="LLM call category (productive, coordination, system)",
    )
    latency_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Round-trip latency in milliseconds from provider base class",
    )
    cache_hit: bool | None = Field(
        default=None,
        description="Whether the provider served this turn from cache",
    )
    retry_count: int | None = Field(
        default=None,
        ge=0,
        description="Number of retry attempts before success",
    )
    retry_reason: NotBlankStr | None = Field(
        default=None,
        description="Exception type name of the last retried error",
    )
    node_types: tuple[NodeType, ...] = Field(
        default=(),
        description="Node types that executed in this turn",
    )
    behavior_tags: tuple[BehaviorTag, ...] = Field(
        default=(),
        description="Behavior categories inferred by BehaviorTaggerMiddleware",
    )
    efficiency_delta: EfficiencyRatios | None = Field(
        default=None,
        description="Efficiency ratios against an ideal baseline",
    )
    prior_tool_call_count: int = Field(
        default=0,
        ge=0,
        description="Cumulative tool calls before this turn (for PTE)",
    )
    tool_response_tokens: int = Field(
        default=0,
        ge=0,
        description="Tokens from tool responses this turn (for PTE)",
    )
    semantic_drift_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Semantic drift similarity score (from SemanticDriftDetector)",
    )

    @model_validator(mode="after")
    def _validate_retry_consistency(self) -> Self:
        """Ensure retry_reason implies retry_count >= 1.

        Returns:
            ``self`` unchanged when retry fields are consistent.

        Raises:
            ValueError: When ``retry_reason`` is set without a
                non-zero ``retry_count``.
        """
        if self.retry_reason is not None and (
            self.retry_count is None or self.retry_count == 0
        ):
            msg = "retry_reason set implies retry_count must be >= 1"
            raise ValueError(msg)
        return self

    @computed_field(description="Total token count")  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens."""
        return self.input_tokens + self.output_tokens

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether this turn completed without error or content filter",
    )
    @property
    def success(self) -> bool:
        """True unless finish_reason is ERROR or CONTENT_FILTER."""
        return self.finish_reason not in (
            FinishReason.ERROR,
            FinishReason.CONTENT_FILTER,
        )
