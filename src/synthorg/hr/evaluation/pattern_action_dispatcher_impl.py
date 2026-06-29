"""Concrete eval-loop pattern action dispatcher.

Routes a proposed remediation action to operators as a notification, so a
cycle that identifies a weakness pattern surfaces an actionable alert
(e.g. "governance weakness -> expand_audit_coverage") instead of stopping
at a bare action identifier in the logs.
"""

from synthorg.core.types import NotBlankStr
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.notifications.protocol import NotificationDispatcherProtocol

_SOURCE: NotBlankStr = NotBlankStr("hr.eval_loop")


class RemediationActionDispatcher:
    """Dispatches a proposed action to operators via a notification sink."""

    __slots__ = ("_notifications",)

    def __init__(
        self,
        *,
        notification_dispatcher: NotificationDispatcherProtocol,
    ) -> None:
        self._notifications = notification_dispatcher

    async def dispatch(
        self,
        action_id: NotBlankStr,
        pattern: NotBlankStr,
    ) -> bool:
        """Route ``action_id`` raised by ``pattern`` to an operator alert.

        Returns:
            ``True``: the recommendation was routed to the notification
            dispatcher (the action is claimed by the operator surface).

        The caller (``EvalLoopCoordinator._dispatch_actions``) owns the
        single ``EVAL_LOOP_ACTION_DISPATCHED`` emission per action, so this
        method does not log it again (double-logging would double-count the
        dispatched/accepted metrics).
        """
        await self._notifications.dispatch(
            Notification(
                category=NotificationCategory.HEALTH,
                severity=NotificationSeverity.WARNING,
                title=NotBlankStr(f"Eval-loop remediation recommended: {action_id}"),
                body=(
                    f"The evaluation loop detected {pattern} and recommends "
                    f"the remediation action '{action_id}'."
                ),
                source=_SOURCE,
                metadata={"action_id": action_id, "pattern": pattern},
            )
        )
        return True
