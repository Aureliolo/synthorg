# module-kind: code
"""What the operator is told when a gate role has nobody holding it.

Three alerts, and they answer different questions on purpose. The standing
gap fires from the cadence and says the org cannot finish anything; the
hire-waiting one fires once per opened request and says there is something
to approve. Sending only the second would mean the operator hears nothing
until a task has already run, been paid for, and stopped.

The third says a hire the operator already approved has been withdrawn
because it can never be completed. It is the one alert about something the
org did rather than something it needs, and it exists because the alternative
was silence: such a request sat approved for seven days, re-failing on every
sweep, on no dashboard page and in no notification.

Kept beside the reconciler rather than inside it: the reconciler's job is
deciding what to sweep and what to open, and the copy an operator reads is
a separate thing to get right.

Sending is best-effort, decided here rather than at each call site: every
one of them has already done the thing being announced (a role marked
warned, a hire request opened and approvable), so a dispatcher fault that
propagated would undo work that succeeded, and in the hire case would let
the next pass open a SECOND request for the same role.
"""

from collections.abc import Callable
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.review_staffing import (
    REVIEW_STAFFING_NOTICE_FAILED,
)

logger = get_logger(__name__)

type DispatcherSource = Callable[[], NotificationDispatcher | None] | None

#: Source recorded on both alerts, matching the reconciler's own actor label
#: so an operator tracing the notice back finds the pass that sent it.
ACTOR: Final[str] = "review-staffing-reconciler"


def hire_request_reason(role: str) -> str:
    """Why the operator is being asked to approve a hire for *role*.

    Copy, not logic: it is shown on the approval item beside the two alerts
    below, so it says the same thing in the same voice as the notice that
    announces it.

    Returns:
        The operator-facing reason recorded on the request.
    """
    return f"No agent holds {role}, so work that needs it parks instead of being done."


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


async def notify_hire_withdrawn(
    notifications: DispatcherSource,
    role: str,
    *,
    reason: str,
) -> None:
    """Say that an approved hire for *role* was withdrawn as uncompletable.

    Carries the reason verbatim, because the operator's next action depends
    on which one it is: a request that named no pair needs a model configured
    for the role, and one whose pair is gone needs the connection back or a
    different pair.

    Args:
        notifications: Late-bound dispatcher source, or ``None`` when
            notifications are not wired.
        role: The role the withdrawn hire was for.
        reason: Why it can never be completed.
    """
    await _dispatch(
        notifications,
        Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.WARNING,
            title=NotBlankStr(f"Withdrew the approved hire for {role}"),
            body=(
                f"You approved a hire for {role}, and it cannot be completed: "
                f"{reason} Retrying would have failed the same way on every "
                "pass, so it has been withdrawn. Nobody holds the role, so "
                "work needing it still parks; fix the cause and a fresh hire "
                "will be opened on the next pass."
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
    try:
        await dispatcher.dispatch(notification)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
        # lint-allow: swallow-ok -- see the module docstring: the announced
        # thing has already happened, so a send that raised must be reported
        # rather than allowed to undo it.
        reraise_critical(exc)
        logger.warning(
            REVIEW_STAFFING_NOTICE_FAILED,
            title=str(notification.title),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = [
    "ACTOR",
    "DispatcherSource",
    "hire_request_reason",
    "notify_hire_waiting",
    "notify_standing_gap",
]
