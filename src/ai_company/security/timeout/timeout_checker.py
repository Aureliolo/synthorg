"""Timeout checker — evaluates pending approvals against timeout policy.

Periodically called (by the engine or a background task) to check
whether pending approval items have exceeded their timeout thresholds
and apply the configured ``TimeoutPolicy``.
"""

from datetime import UTC, datetime

from ai_company.core.approval import ApprovalItem  # noqa: TC001
from ai_company.core.enums import ApprovalStatus, TimeoutActionType
from ai_company.observability import get_logger
from ai_company.observability.events.timeout import (
    TIMEOUT_AUTO_APPROVED,
    TIMEOUT_AUTO_DENIED,
    TIMEOUT_ESCALATED,
    TIMEOUT_POLICY_EVALUATED,
    TIMEOUT_WAITING,
)
from ai_company.security.timeout.models import TimeoutAction  # noqa: TC001
from ai_company.security.timeout.protocol import TimeoutPolicy  # noqa: TC001

logger = get_logger(__name__)


class TimeoutChecker:
    """Evaluates pending approvals against the configured timeout policy.

    Args:
        policy: The timeout policy to apply.
    """

    def __init__(self, *, policy: TimeoutPolicy) -> None:
        self._policy = policy

    async def check(
        self,
        item: ApprovalItem,
    ) -> TimeoutAction:
        """Evaluate a single pending approval item.

        Args:
            item: The approval item to check.

        Returns:
            The ``TimeoutAction`` determined by the policy.
        """
        now = datetime.now(UTC)
        elapsed = (now - item.created_at).total_seconds()

        action = await self._policy.determine_action(item, elapsed)

        event = {
            TimeoutActionType.WAIT: TIMEOUT_WAITING,
            TimeoutActionType.APPROVE: TIMEOUT_AUTO_APPROVED,
            TimeoutActionType.DENY: TIMEOUT_AUTO_DENIED,
            TimeoutActionType.ESCALATE: TIMEOUT_ESCALATED,
        }.get(action.action, TIMEOUT_POLICY_EVALUATED)

        logger.info(
            event,
            approval_id=item.id,
            action_type=item.action_type,
            elapsed_seconds=elapsed,
            timeout_action=action.action.value,
            reason=action.reason,
        )
        return action

    async def check_and_resolve(
        self,
        item: ApprovalItem,
    ) -> tuple[ApprovalItem, TimeoutAction]:
        """Check an approval and return the updated item with the action.

        If the policy returns APPROVE or DENY, the item's status is
        updated accordingly.  WAIT and ESCALATE leave the item in
        PENDING status (escalation is handled by the caller).

        Args:
            item: The approval item to check.

        Returns:
            Tuple of (possibly updated item, timeout action).
        """
        action = await self.check(item)

        if action.action == TimeoutActionType.APPROVE:
            updated = item.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": "timeout_policy",
                    "decision_reason": action.reason,
                },
            )
            return updated, action

        if action.action == TimeoutActionType.DENY:
            updated = item.model_copy(
                update={
                    "status": ApprovalStatus.REJECTED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": "timeout_policy",
                    "decision_reason": action.reason,
                },
            )
            return updated, action

        return item, action
