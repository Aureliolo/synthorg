"""Application state container.

Holds typed references to core services, injected into
``app.state`` at startup and accessed by controllers via
``request.app.state``.
"""

from dataclasses import dataclass

from ai_company.budget.tracker import CostTracker  # noqa: TC001
from ai_company.communication.bus_protocol import MessageBus  # noqa: TC001
from ai_company.config.schema import RootConfig  # noqa: TC001
from ai_company.persistence.protocol import PersistenceBackend  # noqa: TC001


@dataclass(frozen=True, slots=True)
class AppState:
    """Typed application state container.

    Attributes:
        config: Root company configuration.
        persistence: Persistence backend for data access.
        message_bus: Internal message bus.
        cost_tracker: Cost tracking service.
    """

    config: RootConfig
    persistence: PersistenceBackend
    message_bus: MessageBus
    cost_tracker: CostTracker
