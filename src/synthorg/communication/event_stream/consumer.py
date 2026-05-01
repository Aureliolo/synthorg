"""Event stream consumer protocol for projection layers.

The ``EventStreamConsumer`` protocol defines the interface for
components that consume events from ``EventStreamHub``.  The AG-UI
dashboard and the A2A gateway are the two primary consumers.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.communication.event_stream.types import StreamEvent


@runtime_checkable
class EventStreamConsumer(Protocol):
    """Protocol for event stream consumers.

    Each consumer receives ``StreamEvent`` objects from the hub
    and applies its own projection/filtering before delivery.

    Implementations:
    - AG-UI projection (``projector.py``) -- internal dashboard
    - A2A projection -- external federation gateway
    """

    def accepts(self, event: StreamEvent) -> bool:
        """Whether this consumer should receive the event.

        Args:
            event: The stream event to evaluate.

        Returns:
            ``True`` if the consumer wants this event.
        """
        ...

    def project(self, event: StreamEvent) -> dict[str, object]:
        """Project a stream event into the consumer's wire format.

        Args:
            event: The stream event to project.

        Returns:
            Serializable dict in the consumer's expected format.
        """
        ...
