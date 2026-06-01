# module-kind: code
"""Deterministic scripted strategies for the benchmark runner.

These drive the live agent loop with zero real LLM spend. The default
:class:`CleanCompletionStrategy` completes every brief in one turn with a stable
deliverable and a small per-turn cost, so the runner can measure a run's cost
against the company's per-run budget ceiling (the broken-company discriminator).
"""

from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)

# Per-turn cost stamped on the default benchmark completion. Non-zero so the
# runner's budget-ceiling comparison is meaningful; small so a generously
# budgeted (reference) company never trips it.
_DEFAULT_TURN_COST: float = 0.01
_DEFAULT_INPUT_TOKENS: int = 16
_DEFAULT_OUTPUT_TOKENS: int = 8


class CleanCompletionStrategy:
    """Complete every brief in one STOP turn with a stable deliverable."""

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return a deterministic clean completion with a small cost.

        Returns:
            A STOP completion carrying a stable deliverable and turn cost.
        """
        del messages, tools, config
        return CompletionResponse(
            content="Benchmark deliverable: the brief was addressed.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=_DEFAULT_INPUT_TOKENS,
                output_tokens=_DEFAULT_OUTPUT_TOKENS,
                cost=_DEFAULT_TURN_COST,
            ),
            model=model,
        )


__all__ = ["CleanCompletionStrategy"]
