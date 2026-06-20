"""Notification-subsystem error types."""

from typing import ClassVar

from synthorg.core.domain_errors import ConflictError


class NotificationDispatcherUnrestartableError(ConflictError):
    """Raised when ``NotificationDispatcher.start()`` is called after a timed-out close.

    The dispatcher has no background loop, but ``aclose()`` drains
    in-flight ``dispatch()`` calls; a drain that exceeds the hard
    deadline leaves work that may still touch sinks, so the canonical
    lifecycle pattern marks the dispatcher unrestartable rather than
    re-opening it on top of unfinished dispatches. Mirrors
    :class:`~synthorg.providers.errors.ProviderLifecycleConflictError`;
    inherits the shareable ``RESOURCE_CONFLICT`` code.
    """

    default_message: ClassVar[str] = (
        "NotificationDispatcher is unrestartable after a timed-out close"
    )
