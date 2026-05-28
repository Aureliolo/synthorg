"""Stagnation detection protocol.

Defines the ``StagnationDetector`` runtime-checkable protocol that
execution loops call to check for repetitive tool-call patterns.
"""

from typing import Protocol, runtime_checkable

from synthorg.engine.loop_protocol import TurnRecord

from .models import StagnationResult


@runtime_checkable
class StagnationDetector(Protocol):
    """Protocol for intra-loop stagnation detection.

    Implementations analyze recent ``TurnRecord`` entries and return a
    ``StagnationResult`` indicating whether the agent is stuck in a
    repetitive loop.

    Async because future implementations may consult external services
    or LLM-based analysis.
    """

    async def check(
        self,
        turns: tuple[TurnRecord, ...],
        *,
        corrections_injected: int = 0,
    ) -> StagnationResult:
        """Check for stagnation in recent turns.

        Args:
            turns: Ordered turn records from the current execution scope.
            corrections_injected: Number of corrective prompts already
                injected in this execution scope.

        Returns:
            A ``StagnationResult`` with the verdict and supporting data.
        """
        ...

    def get_detector_type(self) -> str:
        """Return the detector type identifier."""
        ...
