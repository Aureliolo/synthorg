"""Provider-agnostic completion-outcome vocabulary.

``FinishReason`` describes why a model stopped generating. It is shared
vocabulary consumed across the engine, execution-trace, budget, and provider
layers, so it lives in a dependency-free ``core`` leaf rather than the heavy
``providers`` hub: a foundation leaf any consumer can import at module level
without dragging the provider package (whose eager init otherwise re-enters
``budget.cost_record`` mid-initialisation).
"""

from enum import StrEnum


class FinishReason(StrEnum):
    """Reason the model stopped generating tokens."""

    STOP = "stop"
    MAX_TOKENS = "max_tokens"
    TOOL_USE = "tool_use"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
