"""Shared token budget tracker for meeting protocol implementations."""

import dataclasses


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
        """Record token usage from an agent call."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
