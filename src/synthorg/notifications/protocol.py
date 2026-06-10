"""NotificationSink protocol for external notification delivery."""

from typing import Protocol, runtime_checkable

from synthorg.notifications.models import Notification


@runtime_checkable
class NotificationSink(Protocol):
    """Protocol for notification delivery adapters.

    Implementations should log errors internally and re-raise so
    the ``NotificationDispatcher`` can track delivery status.
    ``MemoryError`` and ``RecursionError`` must always propagate.

    The ``sink_name`` property is used for logging and diagnostics.
    The ``start()`` / ``close()`` lifecycle pair lets the dispatcher
    deterministically open and tear down sinks that own external
    resources (HTTP clients, SMTP connections, file handles) at the
    boundaries of the application's lifespan. Stateless sinks can
    treat both as no-ops. The protocol matches the contract
    documented in ``docs/design/notifications.md``.
    """

    @property
    def sink_name(self) -> str:
        """Human-readable sink identifier for logging."""
        ...

    async def send(self, notification: Notification) -> None:
        """Deliver a notification.

        Implementations should log errors internally and re-raise
        so ``NotificationDispatcher`` can track delivery status.
        ``MemoryError`` and ``RecursionError`` must always propagate.

        Args:
            notification: The notification to deliver.
        """
        ...

    async def start(self) -> None:
        """Open external resources held by the sink.

        Idempotent: a second call must be a no-op when the sink is
        already started. Stateless sinks (e.g. console) implement
        this as a no-op.
        """
        ...

    async def close(self) -> None:
        """Release external resources held by the sink.

        Idempotent: a second call (or a call before ``start()``)
        must be a no-op. Stateless sinks implement this as a no-op.
        """
        ...
