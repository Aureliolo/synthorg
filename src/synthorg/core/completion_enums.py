"""Provider-agnostic completion-outcome vocabulary.

``FinishReason`` describes why a model stopped generating. It is shared
vocabulary consumed across the engine, execution-trace, budget, and provider
layers, so it lives in a dependency-free ``core`` leaf rather than the heavy
``providers`` hub: a foundation leaf any consumer can import at module level
without dragging the provider package (whose eager init otherwise re-enters
``budget.cost_record`` mid-initialisation).
"""

from enum import StrEnum
from typing import Final


class FinishReason(StrEnum):
    """Reason the model stopped generating tokens."""

    STOP = "stop"
    MAX_TOKENS = "max_tokens"
    TOOL_USE = "tool_use"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


class ReasoningEffort(StrEnum):
    """Provider-agnostic depth of extended reasoning ("thinking") to request.

    Maps 1:1 to LiteLLM's ``reasoning_effort`` request parameter, which each
    provider translates to its own dial (a thinking-token budget on one
    provider family, a reasoning-effort tier on another). The values are
    ordered from cheapest / shallowest to most thorough. A request only carries
    this when the target model advertises reasoning support; otherwise it is
    dropped so a non-reasoning model never receives an unsupported parameter.

    ``StrEnum`` members compare lexicographically, which does NOT match the
    intended cheapest-to-most-thorough order, so any ordinal comparison must go
    through :func:`reasoning_effort_rank`, never ``<`` / ``>`` on the members.
    """

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_REASONING_EFFORT_ORDER: Final[tuple[ReasoningEffort, ...]] = (
    ReasoningEffort.MINIMAL,
    ReasoningEffort.LOW,
    ReasoningEffort.MEDIUM,
    ReasoningEffort.HIGH,
)
"""Cheapest-to-most-thorough order backing :func:`reasoning_effort_rank`."""


def reasoning_effort_rank(effort: ReasoningEffort) -> int:
    """Return the ordinal rank of *effort* (0 = cheapest, higher = deeper).

    Use for ordering comparisons instead of ``<`` / ``>`` on the enum, whose
    ``StrEnum`` members compare lexicographically (which mis-orders the tiers).

    Returns:
        The 0-based rank of *effort* in the cheapest-to-most-thorough order.
    """
    return _REASONING_EFFORT_ORDER.index(effort)
