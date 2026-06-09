"""Narrow completion port for LLM-backed consolidation.

``LLMSynthesisOp`` needs only non-streaming completion from its
provider, not the full ``CompletionProvider`` surface (streaming,
capability lookups).  Depending on this narrow port keeps the
consolidation op decoupled from provider capabilities it never uses and
lets deterministic test doubles conform with a single ``complete``
method.  The concrete ``CompletionProvider`` adapters satisfy it
structurally.
"""

from typing import Protocol, runtime_checkable

from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
)


@runtime_checkable
class CompletionPort(Protocol):
    """Non-streaming completion surface used by consolidation synthesis."""

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Execute a non-streaming chat completion."""
        ...
