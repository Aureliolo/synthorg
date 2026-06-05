"""Prefill Token Equivalents (PTE) approximation.

Hardware-aware efficiency metric for tool-integrated reasoning.
Unlike raw token counts, PTE accounts for KV-cache eviction
between tool calls and long tool-response inflation.

Source: PTE (arXiv:2604.05404).

Formula approximation (no internal KV state required):

    PTE = input_tokens * (1 + eviction_penalty * prior_tool_call_count)
        + output_tokens
        + tool_response_tokens * tool_inflation_factor
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger

logger = get_logger(__name__)


class PTEConfig(BaseModel):
    """Configuration for PTE computation.

    Attributes:
        eviction_penalty: KV-cache eviction cost per prior tool call.
        tool_inflation_factor: Multiplier for tool response tokens
            (tool responses displace more than their own tokens).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    eviction_penalty: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="KV-cache eviction cost per prior tool call",
    )
    tool_inflation_factor: float = Field(
        default=1.5,
        ge=1.0,
        le=5.0,
        description="Multiplier for tool response tokens",
    )


_DEFAULT_CONFIG = PTEConfig()


def prefill_token_equivalents(
    turn: TurnRecord,
    *,
    config: PTEConfig | None = None,
) -> float:
    """Approximate PTE from observable turn data.

    Args:
        turn: A single turn record with token counts.
        config: PTE tuning parameters.  Defaults to ``PTEConfig()``.

    Returns:
        Estimated Prefill Token Equivalents for this turn.
    """
    cfg = config or _DEFAULT_CONFIG
    return (
        turn.input_tokens * (1.0 + cfg.eviction_penalty * turn.prior_tool_call_count)
        + turn.output_tokens
        + turn.tool_response_tokens * cfg.tool_inflation_factor
    )


def compute_trajectory_pte(
    turns: tuple[TurnRecord, ...],
    *,
    config: PTEConfig | None = None,
) -> float:
    """Sum PTE across all turns in a trajectory.

    Args:
        turns: Ordered turn records.
        config: PTE tuning parameters.

    Returns:
        Total PTE for the trajectory.
    """
    cfg = config or _DEFAULT_CONFIG
    return sum(prefill_token_equivalents(t, config=cfg) for t in turns)
