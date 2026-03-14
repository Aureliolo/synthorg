"""Typed protocol for completion providers.

The engine and tests type-hint against ``CompletionProvider`` for loose
coupling.  Concrete adapters and test doubles satisfy it structurally.
"""

from collections.abc import AsyncIterator  # noqa: TC003
from typing import Protocol, runtime_checkable

from .capabilities import ModelCapabilities  # noqa: TC001
from .models import (
    ChatMessage,  # noqa: TC001
    CompletionConfig,  # noqa: TC001
    CompletionResponse,  # noqa: TC001
    StreamChunk,  # noqa: TC001
    ToolDefinition,  # noqa: TC001
)


@runtime_checkable
class CompletionProvider(Protocol):
    """Structural interface every LLM provider adapter must satisfy.

    Defines three async methods: ``complete`` for non-streaming chat
    completion, ``stream`` for streaming completion, and
    ``get_model_capabilities`` for capability metadata lookup.
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
