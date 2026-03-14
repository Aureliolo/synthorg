"""Memory injection strategy protocol and supporting types.

Defines the pluggable ``MemoryInjectionStrategy`` protocol that
controls *how* memories reach agents during execution.  Three
strategies are planned (context injection, tool-based, self-editing);
this module provides the protocol and enums for all, while only
``ContextInjectionStrategy`` (in ``synthorg.memory.retriever``)
is implemented in this release.

``TokenEstimator`` is a local structural protocol that avoids a
``memory → engine`` import cycle (``PromptTokenEstimator`` lives in
``engine/prompt.py``).  Any object with ``estimate_tokens(str) -> int``
satisfies it automatically.
"""

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.providers.models import ChatMessage, ToolDefinition


class InjectionStrategy(StrEnum):
    """Which injection strategy to use for surfacing memories.

    Attributes:
        CONTEXT: Pre-execution context injection (implemented).
        TOOL_BASED: On-demand via agent tools (future).
        SELF_EDITING: Structured read/write memory blocks (future).
    """

    CONTEXT = "context"
    TOOL_BASED = "tool_based"
    SELF_EDITING = "self_editing"


class InjectionPoint(StrEnum):
    """Role of the injected memory message.

    Attributes:
        SYSTEM: Memory injected as a SYSTEM message (default).
        USER: Memory injected as a USER message.
    """

    SYSTEM = "system"
    USER = "user"


@runtime_checkable
class TokenEstimator(Protocol):
    """Token estimation protocol (avoids memory → engine dependency).

    Any object with ``estimate_tokens(str) -> int`` satisfies this
    protocol structurally.
    """

    def estimate_tokens(self, text: str) -> int:
        """Estimate the number of tokens in *text*.

        Implementations must return non-negative values.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count (non-negative).
        """
        ...


class DefaultTokenEstimator:
    """Heuristic token estimator: ``len(text) // 4``.

    Suitable for rough budget enforcement when a model-specific
    tokenizer is unavailable.
    """

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens as ``max(1, len(text) // 4)`` for non-empty text.

        Returns 0 for empty text, at least 1 for any non-empty text.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count (non-negative).
        """
        if not text:
            return 0
        return max(1, len(text) // 4)


@runtime_checkable
class MemoryInjectionStrategy(Protocol):
    """Pluggable strategy for making memories available to agents.

    Implementations determine *how* memories reach the agent:

    - **Context injection**: pre-execution message injection.
    - **Tool-based**: on-demand retrieval via agent tools.
    - **Self-editing**: structured read/write memory blocks.
    """

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        query_text: NotBlankStr,
        token_budget: int,
    ) -> tuple[ChatMessage, ...]:
        """Return memory messages to inject into agent context.

        Context injection returns ranked, formatted memories.
        Tool-based may return empty (tools handle retrieval).
        Self-editing returns the core memory block.

        Args:
            agent_id: The agent requesting memories.
            query_text: Text to use for semantic retrieval.
            token_budget: Maximum tokens for memory content.

        Returns:
            Tuple of ``ChatMessage`` instances (may be empty).
        """
        ...

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return tool definitions this strategy provides.

        Context injection returns ``()``.  Tool-based returns
        recall/search tools.  Self-editing returns read/write tools.

        Returns:
            Tuple of ``ToolDefinition`` instances.
        """
        ...

    @property
    def strategy_name(self) -> str:
        """Human-readable strategy identifier.

        Returns:
            Strategy name string.
        """
        ...
