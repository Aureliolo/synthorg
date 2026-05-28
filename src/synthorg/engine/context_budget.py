"""Context budget indicators and fill estimation.

Provides the ``ContextBudgetIndicator`` model for soft budget display
in system prompts, and ``estimate_context_fill`` for computing the
estimated token fill level of an agent's context window.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.engine.token_estimation import (
    DefaultTokenEstimator,
    PromptTokenEstimator,
)
from synthorg.observability import get_logger
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_FILL_UPDATED,
    CONTEXT_BUDGET_INDICATOR_INJECTED,
)
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.engine.context import AgentContext

logger = get_logger(__name__)

# Estimated tokens per tool definition passed via the API.
_TOOL_DEFINITION_TOKEN_OVERHEAD: int = 50


class ContextBudgetIndicator(BaseModel):
    """Soft budget indicator injected into agent system prompts.

    Attributes:
        fill_tokens: Estimated tokens currently filling the context.
        capacity_tokens: Model's max context window tokens, or
            ``None`` when unknown.
        archived_blocks: Number of archived compaction blocks.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    fill_tokens: int = Field(ge=0, description="Current fill tokens")
    capacity_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Max context window tokens",
    )
    archived_blocks: int = Field(
        default=0,
        ge=0,
        description="Archived compaction blocks",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Context fill percentage",
    )
    @property
    def fill_percent(self) -> float | None:
        """Fill percentage, or ``None`` when capacity is unknown."""
        if self.capacity_tokens is None:
            return None
        return (self.fill_tokens / self.capacity_tokens) * 100.0

    def format(self) -> str:
        """Format as a human-readable indicator string.

        Returns:
            Formatted indicator like
            ``[Context: 12,450/16,000 tokens (78%) | 0 archived blocks]``.
        """
        if self.capacity_tokens is None:
            return (
                f"[Context: {self.fill_tokens:,} tokens "
                f"(capacity unknown) | "
                f"{self.archived_blocks} archived blocks]"
            )
        pct = self.fill_percent
        return (
            f"[Context: {self.fill_tokens:,}/{self.capacity_tokens:,} "
            f"tokens ({pct:.0f}%) | "
            f"{self.archived_blocks} archived blocks]"
        )


def make_context_indicator(
    ctx: AgentContext,
) -> ContextBudgetIndicator:
    """Create a ``ContextBudgetIndicator`` from an ``AgentContext``.

    Derives ``archived_blocks`` from ``compression_metadata`` when
    available.

    Args:
        ctx: Agent context with fill and capacity data.

    Returns:
        Frozen indicator model.
    """
    archived = (
        ctx.compression_metadata.compactions_performed
        if ctx.compression_metadata is not None
        else 0
    )
    indicator = ContextBudgetIndicator(
        fill_tokens=ctx.context_fill_tokens,
        capacity_tokens=ctx.context_capacity_tokens,
        archived_blocks=archived,
    )
    logger.debug(
        CONTEXT_BUDGET_INDICATOR_INJECTED,
        execution_id=ctx.execution_id,
        fill_tokens=indicator.fill_tokens,
        capacity_tokens=indicator.capacity_tokens,
        fill_percent=indicator.fill_percent,
    )
    return indicator


def estimate_context_fill(
    *,
    system_prompt_tokens: int,
    conversation: tuple[ChatMessage, ...],
    tool_definitions_count: int,
    estimator: PromptTokenEstimator | None = None,
) -> int:
    """Estimate total context fill in tokens.

    Sums system prompt tokens, conversation tokens, and tool
    definition overhead.

    Args:
        system_prompt_tokens: Token estimate of the system prompt.
        conversation: Current conversation messages.
        tool_definitions_count: Number of tool definitions passed
            to the LLM (each adds overhead).
        estimator: Token estimator; defaults to
            ``DefaultTokenEstimator``.

    Returns:
        Estimated total fill in tokens.

    Raises:
        ValueError: If ``system_prompt_tokens`` or
            ``tool_definitions_count`` is negative.
    """
    if system_prompt_tokens < 0:
        msg = f"system_prompt_tokens must be >= 0, got {system_prompt_tokens}"
        raise ValueError(msg)
    if tool_definitions_count < 0:
        msg = f"tool_definitions_count must be >= 0, got {tool_definitions_count}"
        raise ValueError(msg)
    est = estimator or DefaultTokenEstimator()
    conversation_tokens = est.estimate_conversation_tokens(conversation)
    tool_overhead = tool_definitions_count * _TOOL_DEFINITION_TOKEN_OVERHEAD
    return system_prompt_tokens + conversation_tokens + tool_overhead


def update_context_fill(
    ctx: AgentContext,
    *,
    system_prompt_tokens: int,
    tool_defs_count: int,
    estimator: PromptTokenEstimator | None = None,
) -> AgentContext:
    """Re-estimate context fill and return updated context.

    Called after each turn to keep the fill estimate current.

    Args:
        ctx: Current agent context.
        system_prompt_tokens: Token estimate of the system prompt.
        tool_defs_count: Number of tool definitions.
        estimator: Token estimator; defaults to
            ``DefaultTokenEstimator``.

    Returns:
        New ``AgentContext`` with updated ``context_fill_tokens``.
    """
    fill = estimate_context_fill(
        system_prompt_tokens=system_prompt_tokens,
        conversation=ctx.conversation,
        tool_definitions_count=tool_defs_count,
        estimator=estimator,
    )
    capacity = ctx.context_capacity_tokens
    new_pct = (fill / capacity) * 100.0 if capacity is not None else None
    logger.debug(
        CONTEXT_BUDGET_FILL_UPDATED,
        execution_id=ctx.execution_id,
        fill_tokens=fill,
        capacity_tokens=capacity,
        fill_percent=new_pct,
    )
    return ctx.with_context_fill(fill)
