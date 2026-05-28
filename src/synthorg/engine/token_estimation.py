"""Token estimation protocol and default heuristic implementation.

Provides the ``PromptTokenEstimator`` protocol for pluggable token
counting and a ``DefaultTokenEstimator`` using the common
``len(text) // 4`` heuristic.  Consumed by ``prompt.py``,
``context_budget.py``, and ``compaction/summarizer.py``.
"""

from typing import Protocol, runtime_checkable

from synthorg.providers.models import ChatMessage


@runtime_checkable
class PromptTokenEstimator(Protocol):
    """Runtime-checkable protocol for estimating token count from text.

    Implementers must define ``estimate_tokens`` and
    ``estimate_conversation_tokens`` methods.
    """

    def estimate_tokens(self, text: str) -> int:
        """Estimate the number of tokens in the given text.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count.
        """
        ...

    def estimate_conversation_tokens(
        self,
        messages: tuple[ChatMessage, ...],
    ) -> int:
        """Estimate the total token count of a conversation.

        Args:
            messages: The conversation messages to estimate.

        Returns:
            Estimated total token count.
        """
        ...


class DefaultTokenEstimator:
    """Heuristic token estimator using character-count approximation.

    Uses the common ``len(text) // 4`` heuristic. Suitable for rough
    estimates; swap in a tiktoken-based estimator for precision.
    """

    _PER_MESSAGE_OVERHEAD: int = 4
    """Overhead tokens per message for role tags and structure."""

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens as approximately 1 token per 4 characters.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count (minimum 0).
        """
        return len(text) // 4

    def estimate_conversation_tokens(
        self,
        messages: tuple[ChatMessage, ...],
    ) -> int:
        """Estimate total tokens across all messages.

        Sums ``len(content) // 4 + overhead`` per message.
        Tool results and tool calls are included in the estimate.

        Args:
            messages: The conversation messages to estimate.

        Returns:
            Estimated total token count (minimum 0).
        """
        total = 0
        for msg in messages:
            content = msg.content or ""
            if msg.tool_result is not None:
                content = msg.tool_result.content or ""
            total += len(content) // 4 + self._PER_MESSAGE_OVERHEAD
            # Tool calls on assistant messages consume tokens
            # (id, name, serialized arguments).
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_tokens = (
                        len(tc.id) // 4
                        + len(tc.name) // 4
                        + len(str(tc.arguments)) // 4
                        + self._PER_MESSAGE_OVERHEAD
                    )
                    total += tc_tokens
        return total
