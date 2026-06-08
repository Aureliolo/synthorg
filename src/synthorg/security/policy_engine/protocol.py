"""PolicyEngine protocol -- runtime policy evaluator interface."""

from typing import Protocol, runtime_checkable

from synthorg.security.policy_engine.models import (
    PolicyActionRequest,
    PolicyDecision,
)


@runtime_checkable
class PolicyEngine(Protocol):
    """Runtime policy evaluator operating on structured action requests.

    Implementations evaluate whether a given action (tool invocation,
    delegation, approval execution) should be allowed or denied based
    on loaded policy definitions.
    """

    @property
    def name(self) -> str:
        """Unique engine name (e.g. ``"cedar"``)."""
        ...

    async def evaluate(
        self,
        request: PolicyActionRequest,
    ) -> PolicyDecision:
        """Evaluate a policy action request.

        Args:
            request: The action to evaluate.

        Returns:
            Allow/deny decision with reason and timing.
        """
        ...
