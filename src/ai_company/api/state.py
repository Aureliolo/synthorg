"""Application state container.

Holds typed references to core services, injected into
``app.state`` at startup and accessed by controllers via
``request.app.state``.
"""

from ai_company.api.approval_store import ApprovalStore  # noqa: TC001
from ai_company.api.auth.service import AuthService  # noqa: TC001
from ai_company.api.errors import ServiceUnavailableError
from ai_company.budget.tracker import CostTracker  # noqa: TC001
from ai_company.communication.bus_protocol import MessageBus  # noqa: TC001
from ai_company.config.schema import RootConfig  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.api import API_SERVICE_UNAVAILABLE
from ai_company.persistence.protocol import PersistenceBackend  # noqa: TC001

logger = get_logger(__name__)


class AppState:
    """Typed application state container.

    Service fields (``persistence``, ``message_bus``, ``cost_tracker``)
    accept ``None`` at construction time for dev/test mode.  Property
    accessors raise ``ServiceUnavailableError`` (HTTP 503) when the
    service is not configured, producing a clear error instead of an
    opaque ``AttributeError``.

    Attributes:
        config: Root company configuration.
        approval_store: In-memory approval queue store.
        startup_time: ``time.monotonic()`` snapshot at app creation.
    """

    __slots__ = (
        "_auth_service",
        "_cost_tracker",
        "_message_bus",
        "_persistence",
        "approval_store",
        "config",
        "startup_time",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        config: RootConfig,
        approval_store: ApprovalStore,
        persistence: PersistenceBackend | None = None,
        message_bus: MessageBus | None = None,
        cost_tracker: CostTracker | None = None,
        auth_service: AuthService | None = None,
        startup_time: float = 0.0,
    ) -> None:
        self.config = config
        self.approval_store = approval_store
        self._persistence = persistence
        self._message_bus = message_bus
        self._cost_tracker = cost_tracker
        self._auth_service = auth_service
        self.startup_time = startup_time

    @property
    def persistence(self) -> PersistenceBackend:
        """Return persistence backend or raise 503."""
        if self._persistence is None:
            logger.warning(
                API_SERVICE_UNAVAILABLE,
                service="persistence",
            )
            msg = "Persistence backend not configured"
            raise ServiceUnavailableError(msg)
        return self._persistence

    @property
    def message_bus(self) -> MessageBus:
        """Return message bus or raise 503."""
        if self._message_bus is None:
            logger.warning(
                API_SERVICE_UNAVAILABLE,
                service="message_bus",
            )
            msg = "Message bus not configured"
            raise ServiceUnavailableError(msg)
        return self._message_bus

    @property
    def cost_tracker(self) -> CostTracker:
        """Return cost tracker or raise 503."""
        if self._cost_tracker is None:
            logger.warning(
                API_SERVICE_UNAVAILABLE,
                service="cost_tracker",
            )
            msg = "Cost tracker not configured"
            raise ServiceUnavailableError(msg)
        return self._cost_tracker

    @property
    def auth_service(self) -> AuthService:
        """Return auth service or raise 503."""
        if self._auth_service is None:
            logger.warning(
                API_SERVICE_UNAVAILABLE,
                service="auth_service",
            )
            msg = "Auth service not configured"
            raise ServiceUnavailableError(msg)
        return self._auth_service
