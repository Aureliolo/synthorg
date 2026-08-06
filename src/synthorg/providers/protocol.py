"""Typed protocol for completion providers.

The engine and tests type-hint against ``CompletionProvider`` for loose
coupling.  Concrete adapters and test doubles satisfy it structurally.
"""

from collections.abc import AsyncIterator, Callable, Mapping
from typing import Protocol, runtime_checkable

from synthorg.core.agent import AgentIdentity

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


type ProviderSelector = Callable[[AgentIdentity], CompletionProvider]
"""Resolve the completion client for an agent's own ``(provider, model)`` pair.

Each agent (the decomposition owner, a plan-review panellist, ...) runs the
session on the provider its identity is bound to, never a shared default, so an
overlapping model id never dispatches to the wrong gateway. The wiring layer
supplies ``registry.get(identity.model.provider)``.
"""

type ConnectionSelector = Callable[[str], CompletionProvider]
"""Resolve the completion client for a named provider connection.

The sibling of :data:`ProviderSelector` for a system feature, which binds a
``(provider, model)`` pair through a setting rather than through an agent
identity. A provider name is a *connection*: it carries its own credentials,
endpoint and quota, so the same model id reached through two of them is two
different calls. A feature therefore resolves the connection its own setting
names at dispatch time, rather than holding whichever client it was
constructed with. The wiring layer supplies ``registry.get``.
"""
