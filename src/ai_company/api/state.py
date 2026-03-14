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
from ai_company.engine.approval_gate import ApprovalGate  # noqa: TC001
from ai_company.engine.coordination.service import MultiAgentCoordinator  # noqa: TC001
from ai_company.engine.task_engine import TaskEngine  # noqa: TC001
from ai_company.hr.registry import AgentRegistryService  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.api import API_APP_STARTUP, API_SERVICE_UNAVAILABLE
from ai_company.persistence.protocol import PersistenceBackend  # noqa: TC001

logger = get_logger(__name__)


class AppState:
    """Typed application state container.

    Service fields (``persistence``, ``message_bus``, ``cost_tracker``,
    ``auth_service``, ``task_engine``, ``coordinator``,
    ``agent_registry``) accept ``None`` at construction time for
    dev/test mode.  Property
    accessors raise ``ServiceUnavailableError`` (HTTP 503) when the
    service is not configured, producing a clear error instead of an
    opaque ``AttributeError``.

    Attributes:
        config: Root company configuration.
        approval_store: In-memory approval queue store.
        startup_time: ``time.monotonic()`` snapshot at app creation.
    """

    __slots__ = (
        "_agent_registry",
        "_approval_gate",
        "_auth_service",
        "_coordinator",
        "_cost_tracker",
        "_message_bus",
        "_persistence",
        "_task_engine",
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
        task_engine: TaskEngine | None = None,
        approval_gate: ApprovalGate | None = None,
        coordinator: MultiAgentCoordinator | None = None,
        agent_registry: AgentRegistryService | None = None,
        startup_time: float = 0.0,
    ) -> None:
        self.config = config
        self.approval_store = approval_store
        self._approval_gate = approval_gate
        self._persistence = persistence
        self._message_bus = message_bus
        self._cost_tracker = cost_tracker
        self._auth_service = auth_service
        self._task_engine = task_engine
        self._coordinator = coordinator
        self._agent_registry = agent_registry
        self.startup_time = startup_time

    def _require_service[T](self, service: T | None, name: str) -> T:
        """Return *service* or raise 503 if not configured.

        Args:
            service: Service instance (``None`` when not configured).
            name: Service name for logging and error message.

        Raises:
            ServiceUnavailableError: If *service* is ``None``.
        """
        if service is None:
            logger.warning(API_SERVICE_UNAVAILABLE, service=name)
            msg = f"{name.replace('_', ' ').title()} not configured"
            raise ServiceUnavailableError(msg)
        return service

    @property
    def persistence(self) -> PersistenceBackend:
        """Return persistence backend or raise 503."""
        return self._require_service(self._persistence, "persistence")

    @property
    def message_bus(self) -> MessageBus:
        """Return message bus or raise 503."""
        return self._require_service(self._message_bus, "message_bus")

    @property
    def cost_tracker(self) -> CostTracker:
        """Return cost tracker or raise 503."""
        return self._require_service(self._cost_tracker, "cost_tracker")

    @property
    def auth_service(self) -> AuthService:
        """Return auth service or raise 503."""
        return self._require_service(self._auth_service, "auth_service")

    @property
    def task_engine(self) -> TaskEngine:
        """Return task engine or raise 503."""
        return self._require_service(self._task_engine, "task_engine")

    @property
    def has_task_engine(self) -> bool:
        """Check whether the task engine is already configured."""
        return self._task_engine is not None

    def set_task_engine(self, engine: TaskEngine) -> None:
        """Set the task engine (deferred initialisation).

        Supports late binding when the task engine is created after
        ``AppState`` construction.

        Args:
            engine: Fully configured task engine.

        Raises:
            RuntimeError: If the task engine was already configured.
        """
        if self._task_engine is not None:
            msg = "Task engine already configured"
            logger.error(API_APP_STARTUP, error=msg)
            raise RuntimeError(msg)
        self._task_engine = engine

    @property
    def approval_gate(self) -> ApprovalGate | None:
        """Return approval gate, or None if not configured."""
        return self._approval_gate

    @property
    def coordinator(self) -> MultiAgentCoordinator:
        """Return coordinator or raise 503."""
        return self._require_service(self._coordinator, "coordinator")

    @property
    def has_coordinator(self) -> bool:
        """Check whether the coordinator is configured."""
        return self._coordinator is not None

    @property
    def agent_registry(self) -> AgentRegistryService:
        """Return agent registry or raise 503."""
        return self._require_service(self._agent_registry, "agent_registry")

    @property
    def has_agent_registry(self) -> bool:
        """Check whether the agent registry is configured."""
        return self._agent_registry is not None

    @property
    def has_auth_service(self) -> bool:
        """Check whether the auth service is already configured."""
        return self._auth_service is not None

    def set_auth_service(self, service: AuthService) -> None:
        """Set the auth service (deferred initialisation).

        Called once during startup after the JWT secret is resolved.

        Args:
            service: Fully configured auth service.

        Raises:
            RuntimeError: If the auth service was already configured.
        """
        if self._auth_service is not None:
            msg = "Auth service already configured"
            logger.error(API_APP_STARTUP, error=msg)
            raise RuntimeError(msg)
        self._auth_service = service
