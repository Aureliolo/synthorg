"""Timeout checker — evaluates pending approvals against timeout policy.

Periodically called (by the engine or a background task) to check
whether pending approval items have exceeded their timeout thresholds
and apply the configured ``TimeoutPolicy``.
"""

from datetime import UTC, datetime

from synthorg.core.approval import ApprovalItem  # noqa: TC001
from synthorg.core.enums import ApprovalStatus, TimeoutActionType
from synthorg.observability import get_logger
from synthorg.observability.events.timeout import (
    TIMEOUT_AUTO_APPROVED,
    TIMEOUT_AUTO_DENIED,
    TIMEOUT_ESCALATED,
    TIMEOUT_POLICY_EVALUATED,
    TIMEOUT_WAITING,
)
from synthorg.security.timeout.models import TimeoutAction
from synthorg.security.timeout.protocol import TimeoutPolicy  # noqa: TC001

logger = get_logger(__name__)

_TIMEOUT_POLICY_DECIDER: str = "timeout_policy"


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

        Raises:
            ValueError: If the item is not in PENDING status.
        """
        if item.status != ApprovalStatus.PENDING:
            msg = (
                f"Cannot check timeout for non-PENDING item "
                f"{item.id!r} (status: {item.status.value!r})"
            )
            raise ValueError(msg)

        now = datetime.now(UTC)
        elapsed = (now - item.created_at).total_seconds()

        try:
            action = await self._policy.determine_action(item, elapsed)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                TIMEOUT_POLICY_EVALUATED,
                approval_id=item.id,
                elapsed_seconds=elapsed,
                note="policy.determine_action failed — defaulting to WAIT",
            )
            action = TimeoutAction(
                action=TimeoutActionType.WAIT,
                reason="Policy evaluation error — defaulting to WAIT",
            )

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
                    "decided_by": _TIMEOUT_POLICY_DECIDER,
                    "decision_reason": action.reason,
                },
            )
            return updated, action

        if action.action == TimeoutActionType.DENY:
            updated = item.model_copy(
                update={
                    "status": ApprovalStatus.REJECTED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": _TIMEOUT_POLICY_DECIDER,
                    "decision_reason": action.reason,
                },
            )
            return updated, action

        return item, action
