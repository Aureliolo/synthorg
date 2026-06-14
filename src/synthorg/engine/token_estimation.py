"""Token estimation protocol and default heuristic implementation.

Provides the ``PromptTokenEstimator`` protocol for pluggable token
counting and a ``DefaultTokenEstimator`` backed by the shared
``synthorg.core.text_estimation.approx_tokens`` heuristic.  Consumed by
``prompt.py``, ``context_budget.py``, and ``compaction/summarizer.py``.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.text_estimation import approx_tokens
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

    Delegates to ``synthorg.core.text_estimation.approx_tokens``.
    Suitable for rough estimates; swap in a tiktoken-based estimator
    for precision.
    """

    _PER_MESSAGE_OVERHEAD: int = 4
    """Overhead tokens per message for role tags and structure."""

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens via the shared chars-per-token heuristic.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count: ``0`` for empty text, otherwise at
            least ``1``.
        """
        return approx_tokens(text)

    def estimate_conversation_tokens(
        self,
        messages: tuple[ChatMessage, ...],
    ) -> int:
        """Estimate total tokens across all messages.

        Sums ``approx_tokens(content) + overhead`` per message via the
        shared chars-per-token heuristic, keeping this estimate aligned
        with ``estimate_tokens``. Tool results and tool calls are
        included in the estimate.

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
            total += approx_tokens(content) + self._PER_MESSAGE_OVERHEAD
            # Tool calls on assistant messages consume tokens
            # (id, name, serialized arguments).
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_tokens = (
                        approx_tokens(tc.id)
                        + approx_tokens(tc.name)
                        + approx_tokens(str(tc.arguments))
                        + self._PER_MESSAGE_OVERHEAD
                    )
                    total += tc_tokens
        return total
