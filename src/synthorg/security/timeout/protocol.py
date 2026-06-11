"""Timeout policy and risk tier classifier protocols."""

from typing import Protocol, runtime_checkable

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.approval import ApprovalItem
from synthorg.security.timeout.models import TimeoutAction


@runtime_checkable
class TimeoutPolicy(Protocol):
    """Protocol for approval timeout policies (see Operations design page).

    Implementations determine what happens when a human does not
    respond to an approval request within a configured timeframe.
    """

    async def determine_action(
        self,
        item: ApprovalItem,
        elapsed_seconds: float,
    ) -> TimeoutAction:
        """Determine the timeout action for a pending approval.

        Args:
            item: The pending approval item.
            elapsed_seconds: Seconds since the item was created.

        Returns:
            The action to take (wait, approve, deny, or escalate).
        """
        ...


# ConfigurableRiskTierClassifier impl in timeout/risk_tier_classifier.py;
# wired via timeout/factory.py and engine/_security_factory.
@runtime_checkable
class RiskTierClassifier(Protocol):
    """Classifies action types into risk tiers for tiered timeouts."""

    def classify(self, action_type: str) -> ApprovalRiskLevel:
        """Classify an action type's risk level.

        Args:
            action_type: The ``category:action`` string.

        Returns:
            The risk tier for timeout policy selection.
        """
        ...
