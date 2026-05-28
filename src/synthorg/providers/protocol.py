"""Typed protocol for completion providers.

The engine and tests type-hint against ``CompletionProvider`` for loose
coupling.  Concrete adapters and test doubles satisfy it structurally.
"""

from collections.abc import AsyncIterator, Mapping
from typing import Protocol, runtime_checkable

from .capabilities import ModelCapabilities
from .models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    ToolDefinition,
)


@runtime_checkable
class CompletionProvider(Protocol):
    """Structural interface every LLM provider adapter must satisfy.

    Defines four async methods: ``complete`` for non-streaming chat
    completion, ``stream`` for streaming completion,
    ``get_model_capabilities`` for a single-model capability lookup, and
    ``batch_get_capabilities`` for many-model capability lookup with
    per-model graceful degradation.
    """

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Execute a non-streaming chat completion.

        Args:
            messages: Conversation history.
            model: Model identifier to use.
            tools: Available tools for function calling.
            config: Optional completion parameters.

        Returns:
            The full completion response.
        """
        ...

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Execute a streaming chat completion.

        Args:
            messages: Conversation history.
            model: Model identifier to use.
            tools: Available tools for function calling.
            config: Optional completion parameters.

        Returns:
            Async iterator of stream chunks.
        """
        ...

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Return capability metadata for the given model.

        Args:
            model: Model identifier.

        Returns:
            Static capability and cost information.
        """
        ...

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        """Return capability metadata for many models in one call.

        Failures degrade per-model: models whose lookup fails surface as
        ``None`` entries so callers preserve graceful per-model fallback.
        The returned mapping keys are exactly the input ``models`` tuple.

        Args:
            models: Tuple of model identifiers to look up.

        Returns:
            Mapping from model id to capabilities (or ``None`` on failure).
        """
        ...
