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


class ReasoningEffort(StrEnum):
    """Provider-agnostic depth of extended reasoning ("thinking") to request.

    Maps 1:1 to LiteLLM's ``reasoning_effort`` request parameter, which each
    provider translates to its own dial (an Anthropic thinking-token budget,
    an OpenAI reasoning effort). The values are ordered from cheapest /
    shallowest to most thorough. A request only carries this when the target
    model advertises reasoning support; otherwise it is dropped so a
    non-reasoning model never receives an unsupported parameter.
    """

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
