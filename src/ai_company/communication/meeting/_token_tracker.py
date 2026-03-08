"""Shared token budget tracker for meeting protocol implementations.

Concurrency note: ``TokenTracker`` is safe for use within a single
``asyncio`` event loop (cooperative multitasking).  ``record()`` runs
to completion without suspension, so concurrent coroutines sharing a
tracker will not interleave reads and writes.  However, intermediate
values of ``remaining`` during a parallel ``TaskGroup`` phase reflect
only the tasks that have completed so far — callers should pre-divide
budgets before launching parallel work rather than checking
``remaining`` inside concurrent tasks.
"""

import dataclasses

from ai_company.observability import get_logger
from ai_company.observability.events.meeting import (
    MEETING_BUDGET_EXHAUSTED,
    MEETING_VALIDATION_FAILED,
)

logger = get_logger(__name__)


@dataclasses.dataclass
class TokenTracker:
    """Mutable token budget tracker scoped to a single meeting execution.

    Attributes:
        budget: Total token budget for the meeting.
        input_tokens: Total prompt tokens consumed so far.
        output_tokens: Total response tokens generated so far.
    """

    budget: int
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        """Validate budget is positive."""
        if self.budget <= 0:
            msg = f"budget must be positive, got {self.budget}"
            raise ValueError(msg)

    @property
    def used(self) -> int:
        """Total tokens consumed so far."""
        return self.input_tokens + self.output_tokens

    @property
    def remaining(self) -> int:
        """Tokens remaining in the budget."""
        return max(0, self.budget - self.used)

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
                MEETING_VALIDATION_FAILED,
                error=msg,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            raise ValueError(msg)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

        if self.used > self.budget:
            logger.warning(
                MEETING_BUDGET_EXHAUSTED,
                tokens_used=self.used,
                token_budget=self.budget,
                overage=self.used - self.budget,
            )
