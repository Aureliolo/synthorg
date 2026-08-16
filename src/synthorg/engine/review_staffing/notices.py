# module-kind: code
"""What the operator is told when a gate role has nobody holding it.

Two alerts, and they answer different questions on purpose. The standing
gap fires from the cadence and says the org cannot finish anything; the
hire-waiting one fires once per opened request and says there is something
to approve. Sending only the second would mean the operator hears nothing
until a task has already run, been paid for, and stopped.

Kept beside the reconciler rather than inside it: the reconciler's job is
deciding what to sweep and what to open, and the copy an operator reads is
a separate thing to get right.
"""

from collections.abc import Callable
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)

type DispatcherSource = Callable[[], NotificationDispatcher | None] | None

#: Source recorded on both alerts, matching the reconciler's own actor label
#: so an operator tracing the notice back finds the pass that sent it.
ACTOR: Final[str] = "review-staffing-reconciler"


async def notify_standing_gap(notifications: DispatcherSource, role: str) -> None:
    """Say that nobody holds *role*, before any work has needed it.

    Args:
        notifications: Late-bound dispatcher source, or ``None`` when
            notifications are not wired.
        role: The unstaffed gate role.
    """
    await _dispatch(
        notifications,
        Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.WARNING,
            title=NotBlankStr(f"No agent holds {role}"),
            body=(
                f"Completion gates need {role} to sign work off, and nobody on "
                "the roster holds it, so finished work will park instead of "
                "completing. Give an existing agent the role, or hire one."
            ),
            source=NotBlankStr(ACTOR),
            metadata={"role": role},
        ),
    )


async def notify_hire_waiting(notifications: DispatcherSource, role: str) -> None:
    """Say that *role* is unstaffed and a hire is waiting for approval.

    Sent once per opened request rather than once per pass: the request is
    the thing needing an answer, and repeating the same alert every cadence
    trains the operator to ignore it.

    Args:
        notifications: Late-bound dispatcher source, or ``None`` when
            notifications are not wired.
        role: The unstaffed role.
    """
    await _dispatch(
        notifications,
        Notification(
            category=NotificationCategory.APPROVAL,
            severity=NotificationSeverity.WARNING,
            title=NotBlankStr(f"No agent holds {role}"),
            body=(
                f"Completion gates needing {role} are parking work instead of "
                "reviewing it. A hire is waiting for your approval; giving an "
                "existing agent the role resolves it too."
            ),
            source=NotBlankStr(ACTOR),
            metadata={"role": role},
        ),
    )


async def _dispatch(
    notifications: DispatcherSource, notification: Notification
) -> None:
    """Send *notification* if a dispatcher is available right now.

    The source is called per send rather than captured: a settings write
    that rewires notifications closes the one that was current, so a held
    instance is already shut by the time the first role goes unstaffed.
    """
    if notifications is None:
        return
    dispatcher = notifications()
    if dispatcher is None:
        return
    await dispatcher.dispatch(notification)


__all__ = [
    "ACTOR",
    "DispatcherSource",
    "notify_hire_waiting",
    "notify_standing_gap",
]
