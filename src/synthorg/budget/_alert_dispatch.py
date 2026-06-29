# module-kind: code
"""Shared best-effort dispatch for budget WARNING notifications."""

from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger, safe_error_description

logger = get_logger(__name__)


async def dispatch_budget_alert(
    dispatcher: NotificationDispatcher,
    *,
    title: str,
    body: str,
    on_failure_event: str,
    **failure_context: object,
) -> bool:
    """Dispatch a budget WARNING notification.

    A dispatch failure is logged with ``on_failure_event`` (plus any
    ``failure_context``) and swallowed so a flaky sink never breaks the caller;
    ``MemoryError`` / ``RecursionError`` re-raise first. The boolean return lets
    a rate-limited caller refund its admission slot when the dispatch failed.

    Args:
        dispatcher: Notification dispatcher.
        title: Notification title.
        body: Human-readable alert body.
        on_failure_event: Event constant logged when dispatch fails.
        failure_context: Extra structured fields for the failure log.

    Returns:
        ``True`` when the notification was dispatched; ``False`` on a swallowed
        failure.
    """
    from synthorg.notifications.models import (  # noqa: PLC0415
        Notification,
        NotificationCategory,
        NotificationSeverity,
    )

    try:
        await dispatcher.dispatch(
            Notification(
                category=NotificationCategory.BUDGET,
                severity=NotificationSeverity.WARNING,
                title=title,
                body=body,
                source="budget.call_analytics",
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            on_failure_event,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            **failure_context,
        )
        return False
    return True
