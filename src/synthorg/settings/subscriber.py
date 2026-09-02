"""Settings change subscriber protocol.

Defines the interface for services that react to runtime setting
changes dispatched by :class:`SettingsChangeDispatcher`.
"""

from collections.abc import Sequence
from typing import Final, Protocol, runtime_checkable

#: One settings write, as the ``(namespace, key)`` pair that identifies it.
type SettingChange = tuple[str, str]

# How many keys a batch names in a log label before it stops listing them. A
# form save carries every field an operator touched, so naming a few and
# counting the rest keeps the line readable without under-reporting the batch.
_NAMED_CHANGES: Final[int] = 3


def describe_changes(changes: Sequence[SettingChange]) -> str:
    """Render a batch of changes as a log label.

    Args:
        changes: The pairs to render.

    Returns:
        Up to :data:`_NAMED_CHANGES` dotted keys in publication order, plus a
        count of any remainder.
    """
    keys = [f"{namespace}.{key}" for namespace, key in changes]
    named = ",".join(keys[:_NAMED_CHANGES])
    rest = len(keys) - _NAMED_CHANGES
    return named if rest <= 0 else f"{named}+{rest}"


@runtime_checkable
class BootAppliedSettingsSubscriber(Protocol):
    """A subscriber whose value is baked into something built before it ran.

    A live holder seeded from the environment config at construction carries
    the environment's value until the first watched WRITE, so a value an
    operator persisted before the last restart is inert on a cold boot for as
    long as nobody writes. The dispatcher calls :meth:`apply_persisted` once
    when it starts, after the settings backend is reachable, so the persisted
    value is applied on the same path a write would take.
    """

    async def apply_persisted(self) -> None:
        """Apply the persisted values of the watched keys as a write would."""
        ...


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
    def watched_keys(self) -> frozenset[SettingChange]:
        """Return the set of (namespace, key) pairs this subscriber watches."""
        ...

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        ...

    async def on_settings_changed(
        self,
        changes: Sequence[SettingChange],
    ) -> None:
        """Handle a batch of setting changes.

        A batch, not a single pair, because an operator saving a form writes
        a form's worth of keys at once and a subscriber that rebuilds a
        subsystem should rebuild once for the lot rather than once per field.
        A subscriber whose reaction is genuinely per key iterates *changes*;
        one whose reaction is a single re-read or rebuild ignores the
        contents and acts once.

        Implementations must be idempotent. Errors are caught by the
        dispatcher: they do not crash the polling loop.

        Args:
            changes: The watched pairs that changed, already filtered to
                this subscriber's :attr:`watched_keys` and never empty.
                Ordered as the writes were published, and deduplicated, so
                a key written twice inside one window appears once.
        """
        ...
