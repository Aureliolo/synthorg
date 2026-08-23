"""Per-round token budget for a multi-party conversation.

Concurrency note: ``TokenTracker`` is safe for use within a single
``asyncio`` event loop (cooperative multitasking).  ``record()`` runs
to completion without suspension, so concurrent coroutines sharing a
tracker will not interleave reads and writes.  However, intermediate
values of ``remaining`` during a parallel ``TaskGroup`` phase reflect
only the tasks that have completed so far -- callers should pre-divide
budgets before launching parallel work rather than checking
``remaining`` inside concurrent tasks.
"""

from typing import override

from synthorg.observability import get_logger
from synthorg.observability.events.multi_agent import (
    MULTI_AGENT_BUDGET_EXHAUSTED,
    MULTI_AGENT_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class TokenTracker:
    """Token budget scoped to a single conversation run.

    Consumption only ever grows, and only through :meth:`record`, which
    is where the non-negative check lives. The totals are therefore read
    through properties rather than exposed as writable attributes: an
    assignment could otherwise move a tally backwards and leave every
    later ``remaining`` reading a budget nothing spent.
    """

    __slots__ = ("_budget", "_input_tokens", "_output_tokens")

    def __init__(self, *, budget: int) -> None:
        """Open a run with *budget* tokens to spend.

        Args:
            budget: Total token budget for the conversation.

        Raises:
            ValueError: If ``budget`` is not positive.
        """
        if budget <= 0:
            msg = f"budget must be positive, got {budget}"
            raise ValueError(msg)
        self._budget = budget
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def budget(self) -> int:
        """Total token budget for the conversation."""
        return self._budget

    @property
    def input_tokens(self) -> int:
        """Total prompt tokens consumed so far."""
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        """Total response tokens generated so far."""
        return self._output_tokens

    @property
    def used(self) -> int:
        """Total tokens consumed so far."""
        return self._input_tokens + self._output_tokens

    @property
    def remaining(self) -> int:
        """Tokens remaining in the budget."""
        return max(0, self._budget - self.used)

    @property
    def is_exhausted(self) -> bool:
        """Whether the budget is fully consumed."""
        return self.remaining == 0

    def record(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage from an agent call.

        Logs a warning when token usage exceeds the budget after
        recording.

        Args:
            input_tokens: Prompt tokens consumed (must be >= 0).
            output_tokens: Response tokens generated (must be >= 0).

        Raises:
            ValueError: If either token count is negative.
        """
        if input_tokens < 0 or output_tokens < 0:
            msg = (
                f"Token counts must be non-negative, got "
                f"input_tokens={input_tokens}, "
                f"output_tokens={output_tokens}"
            )
            logger.warning(
                MULTI_AGENT_VALIDATION_FAILED,
                error=msg,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            raise ValueError(msg)
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens

        if self.used > self._budget:
            logger.warning(
                MULTI_AGENT_BUDGET_EXHAUSTED,
                tokens_used=self.used,
                token_budget=self._budget,
                overage=self.used - self._budget,
            )

    @override
    def __repr__(self) -> str:
        """Return the budget and what has been spent against it.

        Returns:
            A ``TokenTracker(...)`` string carrying budget and totals.
        """
        return (
            f"TokenTracker(budget={self._budget}, "
            f"input_tokens={self._input_tokens}, "
            f"output_tokens={self._output_tokens})"
        )


__all__ = ["TokenTracker"]
