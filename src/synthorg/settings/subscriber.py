"""Settings change subscriber protocol.

Defines the interface for services that react to runtime setting
changes dispatched by :class:`SettingsChangeDispatcher`.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SettingsSubscriber(Protocol):
    """Structural interface for settings change subscribers.

    Implementations declare which ``(namespace, key)`` pairs they
    watch and provide a callback invoked by the
    :class:`~synthorg.settings.dispatcher.SettingsChangeDispatcher`
    when a matching change is detected.

    Every change the dispatcher delivers is one a subscriber can act on:
    a ``compose_set`` setting is rejected on the write side, so it never
    publishes a change.

    Attributes:
        watched_keys: ``(namespace, key)`` pairs this subscriber
            cares about.
        subscriber_name: Human-readable name for logging.
    """

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the set of (namespace, key) pairs this subscriber watches."""
        ...

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        ...

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Handle a setting change notification.

        Implementations must be idempotent.  Errors are caught by the
        dispatcher -- they do not crash the polling loop.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        ...
