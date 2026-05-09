"""Extended service accessors for ``AppState``.

Properties and setters for settings, auth, session, providers, ontology,
backup, integrations, escalation, A2A, and MCP services.  Extracted from
``state.py`` to keep that module's size under the project limit.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import threading
    from collections import OrderedDict
    from collections.abc import AsyncIterator

from synthorg.api.auth.presence import UserPresence  # noqa: TC001
from synthorg.api.auth.service import AuthService  # noqa: TC001
from synthorg.api.auth.ticket_store import WsTicketStore  # noqa: TC001
from synthorg.api.rate_limits.config import PerOpRateLimitConfig  # noqa: TC001
from synthorg.api.rate_limits.inflight_config import (
    PerOpConcurrencyConfig,  # noqa: TC001
)
from synthorg.api.services.org_mutations import OrgMutationService  # noqa: TC001
from synthorg.api.services.workflow_rollback_service import (
    WorkflowRollbackService,  # noqa: TC001
)
from synthorg.api.state_services_facades import _FacadesMixin
from synthorg.backup.service import BackupService  # noqa: TC001
from synthorg.communication.conflict_resolution.escalation.notify import (
    EscalationNotifySubscriber,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,  # noqa: TC001
    EscalationQueueStore,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,  # noqa: TC001
)
from synthorg.communication.conflict_resolution.escalation.sweeper import (
    EscalationExpirationSweeper,  # noqa: TC001
)
from synthorg.communication.delegation.record_store import (
    DelegationRecordStore,  # noqa: TC001
)
from synthorg.hr.training.plan_service import TrainingPlanService  # noqa: TC001
from synthorg.hr.training.service import TrainingService  # noqa: TC001
from synthorg.memory.embedding.fine_tune_orchestrator import (
    FineTuneOrchestrator,  # noqa: TC001
)
from synthorg.notifications.dispatcher import (
    NotificationDispatcher,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_BRIDGE_CONFIG_REJECTED,
    REQUEST_LOCK_RELEASE_SKIPPED_WHILE_HELD,
)
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED
from synthorg.ontology.drift.service import DriftDetectionService  # noqa: TC001
from synthorg.ontology.drift.store import DriftReportStore  # noqa: TC001
from synthorg.ontology.service import OntologyService  # noqa: TC001
from synthorg.ontology.sync import OntologyOrgMemorySync  # noqa: TC001
from synthorg.persistence.auth_protocol import (
    LockoutRepository as LockoutStore,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    RefreshTokenRepository as RefreshStore,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    SessionRepository as SessionStore,  # noqa: TC001
)
from synthorg.providers.health import ProviderHealthTracker  # noqa: TC001
from synthorg.providers.management.audit_service import (
    ProviderAuditService,  # noqa: TC001
)
from synthorg.providers.management.preset_override_service import (
    PresetOverrideService,  # noqa: TC001
)
from synthorg.providers.management.service import (
    ProviderManagementService,  # noqa: TC001
)
from synthorg.providers.registry import ProviderRegistry  # noqa: TC001
from synthorg.providers.routing.router import ModelRouter  # noqa: TC001
from synthorg.settings.bridge_configs import ApiBridgeConfig  # noqa: TC001
from synthorg.settings.resolver import ConfigResolver  # noqa: TC001
from synthorg.settings.service import SettingsService  # noqa: TC001
from synthorg.tools.invocation_tracker import ToolInvocationTracker  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.a2a.agent_card import AgentCardBuilder
    from synthorg.a2a.client import A2AClient
    from synthorg.a2a.peer_registry import PeerRegistry
    from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge
    from synthorg.integrations.connections.catalog import ConnectionCatalog
    from synthorg.integrations.health.prober import HealthProberService
    from synthorg.integrations.mcp_catalog.installations import (
        McpInstallationRepository,
    )
    from synthorg.integrations.mcp_catalog.service import CatalogService
    from synthorg.integrations.oauth.state_service import OAuthStateService
    from synthorg.integrations.oauth.token_manager import OAuthTokenManager
    from synthorg.integrations.tunnel.protocol import TunnelProvider
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)

# Defence-in-depth cap on the per-AppState request-lock registry.
# ``scope_request`` retains the lock across handler exit (the next
# approve/reject for the same id needs it), so an authenticated
# client that scopes unique ids and never advances them would
# otherwise grow the dict forever. 10k is well above any realistic
# in-flight working set for a single org.
_MAX_REQUEST_LOCKS: int = 10_000


def _reject_non_int(value: object, *, field: str) -> None:
    """Raise ``TypeError`` (with a structured warning) for non-int settings.

    The WS DoS-prevention setters expect ``int`` values resolved from
    ``ConfigResolver.get_int``; non-int values would otherwise raise
    ``TypeError`` at the bounds comparison without a structured log,
    leaving operators without a clear signal which knob was bad.
    """
    # ``isinstance(value, int)`` accepts ``bool`` (since ``bool`` is a
    # subclass of ``int`` in Python); explicitly reject it so flags
    # don't slip through as 0/1.
    if isinstance(value, bool) or not isinstance(value, int):
        logger.warning(
            API_BRIDGE_CONFIG_REJECTED,
            field=field,
            reason="invalid_type",
            provided_type=type(value).__name__,
        )
        msg = f"{field} must be int, got {type(value).__name__}"
        raise TypeError(msg)


class AppStateServicesMixin(_FacadesMixin):
    """Service accessor mixin for ``AppState``.

    Every property and setter in this mixin relies on private
    ``_*`` attributes allocated in ``AppState.__slots__`` and
    the ``_require_service`` / ``_set_once`` / ``_init_derived_services``
    helpers declared on the concrete class.

    The facade-service accessors (signals / analytics / reports /
    communication / infrastructure / organization / integration /
    quality) live in
    :class:`~synthorg.api.state_services_facades._FacadesMixin` to keep
    this module under the 800-line size limit.
    """

    _set_once: Any
    _init_derived_services: Any
    _api_bridge_config: ApiBridgeConfig
    config: Any

    def _require_service[T](  # pragma: no cover
        self, service: T | None, name: str
    ) -> T:
        """Return *service* or raise (implemented on concrete class)."""
        raise NotImplementedError

    # Slot attrs the mixin reads directly (populated on concrete class).
    _settings_service: SettingsService | None
    _fine_tune_orchestrator: FineTuneOrchestrator | None
    _config_resolver: ConfigResolver | None
    _org_mutation_service: OrgMutationService | None
    _provider_management: ProviderManagementService | None
    _provider_audit_service: ProviderAuditService | None
    _preset_override_service: PresetOverrideService | None
    _provider_health_tracker: ProviderHealthTracker | None
    _tool_invocation_tracker: ToolInvocationTracker | None
    _training_service: TrainingService | None
    _training_plan_service: TrainingPlanService | None
    _workflow_rollback_service: WorkflowRollbackService | None
    _memory_backend: MemoryBackend | None
    _delegation_record_store: DelegationRecordStore | None
    _auth_service: AuthService | None
    _ticket_store: WsTicketStore
    _session_store: SessionStore | None
    _lockout_store: LockoutStore | None
    _refresh_store: RefreshStore | None
    _user_presence: UserPresence
    _provider_registry: ProviderRegistry | None
    _notification_dispatcher: NotificationDispatcher | None
    _bridge_config_applied: bool
    _ontology_service: OntologyService | None
    _drift_report_store: DriftReportStore | None
    _drift_detection_service: DriftDetectionService | None
    _ontology_sync_service: OntologyOrgMemorySync | None
    _model_router: ModelRouter | None
    _backup_service: BackupService | None
    _connection_catalog: ConnectionCatalog | None
    _tunnel_provider: TunnelProvider | None
    _oauth_token_manager: OAuthTokenManager | None
    _oauth_state_service: OAuthStateService | None
    _health_prober_service: HealthProberService | None
    _webhook_event_bridge: WebhookEventBridge | None
    _escalation_store: EscalationQueueStore | None
    _escalation_registry: PendingFuturesRegistry | None
    _escalation_processor: DecisionProcessor | None
    _escalation_sweeper: EscalationExpirationSweeper | None
    _escalation_notify_subscriber: EscalationNotifySubscriber | None
    _a2a_card_builder: AgentCardBuilder | None
    _a2a_client: A2AClient | None
    _a2a_peer_registry: PeerRegistry | None
    _mcp_catalog_service: CatalogService | None
    _mcp_installations_repo: McpInstallationRepository | None
    _persistence: Any
    _ws_auth_timeout_seconds: float
    _ws_frame_timeout_seconds: int
    _ws_revalidation_window_seconds: int
    _ws_revalidation_max_failures: int
    _request_locks: OrderedDict[str, asyncio.Lock]
    _request_locks_guard: threading.Lock
    _request_lock_refs: dict[str, int]

    @property
    def settings_service(self) -> SettingsService:
        """Return settings service or raise 503."""
        return self._require_service(self._settings_service, "settings_service")

    @property
    def has_settings_service(self) -> bool:
        """Check whether the settings service is configured."""
        return self._settings_service is not None

    @property
    def fine_tune_orchestrator(self) -> FineTuneOrchestrator:
        """Return fine-tune orchestrator or raise 503."""
        return self._require_service(
            self._fine_tune_orchestrator,
            "fine_tune_orchestrator",
        )

    @property
    def has_fine_tune_orchestrator(self) -> bool:
        """Check whether the fine-tune orchestrator is configured."""
        return self._fine_tune_orchestrator is not None

    def set_fine_tune_orchestrator(
        self,
        orchestrator: FineTuneOrchestrator,
    ) -> None:
        """Attach the fine-tune orchestrator (once-only)."""
        self._set_once(
            "_fine_tune_orchestrator",
            orchestrator,
            "Fine-tune orchestrator",
        )

    @property
    def has_config_resolver(self) -> bool:
        """Check whether the config resolver is configured."""
        return self._config_resolver is not None

    @property
    def config_resolver(self) -> ConfigResolver:
        """Return the cached config resolver or raise 503."""
        return self._require_service(self._config_resolver, "config_resolver")

    @property
    def has_org_mutation_service(self) -> bool:
        """Check whether the org mutation service is configured."""
        return self._org_mutation_service is not None

    @property
    def org_mutation_service(self) -> OrgMutationService:
        """Return the org mutation service or raise 503."""
        return self._require_service(
            self._org_mutation_service,
            "org_mutation_service",
        )

    @property
    def provider_management(self) -> ProviderManagementService:
        """Return provider management service or raise 503."""
        return self._require_service(
            self._provider_management,
            "provider_management",
        )

    @property
    def has_provider_management(self) -> bool:
        """Check whether the provider management service is configured."""
        return self._provider_management is not None

    @property
    def provider_audit_service(self) -> ProviderAuditService:
        """Return the provider mutation audit service or raise 503.

        Returns the service that owns provider audit-log writes and
        keyset-paginated reads.  Raises ``ServiceUnavailableError``
        (HTTP 503) when the persistence backend has not been wired
        (in-memory fallback paths, pre-bootstrap rigs).  Most call
        sites SHOULD prefer ``has_provider_audit_service`` first.
        """
        return self._require_service(
            self._provider_audit_service,
            "provider_audit_service",
        )

    @property
    def has_provider_audit_service(self) -> bool:
        """Check whether the provider audit service is wired."""
        return self._provider_audit_service is not None

    @property
    def preset_override_service(self) -> PresetOverrideService:
        """Return the preset override service or raise 503.

        ``None`` when the persistence backend has not been wired
        (in-memory fallback paths).  Callers should prefer
        ``has_preset_override_service`` first.
        """
        return self._require_service(
            self._preset_override_service,
            "preset_override_service",
        )

    @property
    def has_preset_override_service(self) -> bool:
        """Check whether the preset override service is wired."""
        return self._preset_override_service is not None

    @property
    def provider_health_tracker(self) -> ProviderHealthTracker:
        """Return provider health tracker or raise 503."""
        return self._require_service(
            self._provider_health_tracker,
            "provider_health_tracker",
        )

    @property
    def has_provider_health_tracker(self) -> bool:
        """Check whether the provider health tracker is configured."""
        return self._provider_health_tracker is not None

    @property
    def has_tool_invocation_tracker(self) -> bool:
        """Check whether the tool invocation tracker is configured."""
        return self._tool_invocation_tracker is not None

    @property
    def tool_invocation_tracker(self) -> ToolInvocationTracker:
        """Return tool invocation tracker or raise 503."""
        return self._require_service(
            self._tool_invocation_tracker,
            "tool_invocation_tracker",
        )

    @property
    def has_training_service(self) -> bool:
        """Check whether the training service is configured."""
        return self._training_service is not None

    @property
    def training_service(self) -> TrainingService:
        """Return training service or raise 503."""
        return self._require_service(
            self._training_service,
            "training_service",
        )

    def set_training_service(self, service: TrainingService) -> None:
        """Attach the training service (once-only)."""
        self._set_once("_training_service", service, "Training service")

    @property
    def has_training_plan_service(self) -> bool:
        """Check whether the training plan service is configured."""
        return self._training_plan_service is not None

    @property
    def training_plan_service(self) -> TrainingPlanService:
        """Return training plan service or raise 503.

        ``TrainingPlanService`` is the audit-aware facade over the
        ``training_plans`` + ``training_results`` repositories.  The
        ``TrainingController`` routes every plan-CRUD write through
        this service so audit logging cannot regress when a new write
        path is added.
        """
        return self._require_service(
            self._training_plan_service,
            "training_plan_service",
        )

    def set_training_plan_service(self, service: TrainingPlanService) -> None:
        """Attach the training plan service (once-only)."""
        self._set_once("_training_plan_service", service, "Training plan service")

    @property
    def has_workflow_rollback_service(self) -> bool:
        """Check whether the workflow rollback service is configured."""
        return self._workflow_rollback_service is not None

    @property
    def workflow_rollback_service(self) -> WorkflowRollbackService:
        """Return workflow rollback service or raise 503.

        ``WorkflowRollbackService`` centralises the live save +
        post-rollback snapshot writes the controller previously made
        directly on the workflow_definitions repository, so audit
        logging cannot regress when a new write path lands in the
        rollback contract.
        """
        return self._require_service(
            self._workflow_rollback_service,
            "workflow_rollback_service",
        )

    def set_workflow_rollback_service(self, service: WorkflowRollbackService) -> None:
        """Attach the workflow rollback service (once-only)."""
        self._set_once(
            "_workflow_rollback_service", service, "Workflow rollback service"
        )

    @property
    def has_memory_backend(self) -> bool:
        """Check whether a shared MemoryBackend is configured."""
        return self._memory_backend is not None

    @property
    def memory_backend(self) -> MemoryBackend:
        """Return the shared memory backend or raise 503."""
        return self._require_service(
            self._memory_backend,
            "memory_backend",
        )

    def set_memory_backend(self, backend: MemoryBackend) -> None:
        """Attach the shared memory backend (once-only)."""
        self._set_once("_memory_backend", backend, "Memory backend")

    @property
    def has_delegation_record_store(self) -> bool:
        """Check whether the delegation record store is configured."""
        return self._delegation_record_store is not None

    @property
    def delegation_record_store(self) -> DelegationRecordStore:
        """Return delegation record store or raise 503."""
        return self._require_service(
            self._delegation_record_store,
            "delegation_record_store",
        )

    @property
    def has_auth_service(self) -> bool:
        """Check whether the auth service is already configured."""
        return self._auth_service is not None

    @property
    def ticket_store(self) -> WsTicketStore:
        """Return the WebSocket ticket store (always available)."""
        return self._ticket_store

    def get_or_create_request_lock(self, request_id: str) -> asyncio.Lock:
        """Return the per-request lifecycle lock, creating it if absent.

        Low-level primitive that exposes the cached Lock for tests and
        diagnostics. Production callers MUST go through
        :meth:`acquire_request_lock` instead, which pairs this with a
        refcount bump so a concurrent eviction sweep cannot drop the
        entry between receiving the Lock and entering ``async with``.

        The dict is guarded by a plain ``threading.Lock`` because
        ``asyncio.Lock`` instances can only be constructed inside a
        running event loop, so the registry needs a thread-safe
        "check, then create" that does not require an active loop to
        serialise itself.

        On insert, the registry is capped at ``_MAX_REQUEST_LOCKS``: if
        adding the new entry would exceed the cap, the oldest **idle**
        entries are evicted (still-held or in-flight locks are kept so
        an in-flight approve/reject never strands a waiter on an
        evicted Lock). The cap defends against an authenticated client
        that scopes unique ids and never advances them to a terminal
        state, which would otherwise grow the dict without bound.
        """
        lock = self._request_locks.get(request_id)
        if lock is not None:
            return lock
        with self._request_locks_guard:
            lock = self._request_locks.get(request_id)
            if lock is None:
                lock = asyncio.Lock()
                self._request_locks[request_id] = lock
                if len(self._request_locks) > _MAX_REQUEST_LOCKS:
                    self._evict_idle_request_locks_locked(_MAX_REQUEST_LOCKS)
            return lock

    @asynccontextmanager
    async def acquire_request_lock(self, request_id: str) -> AsyncIterator[None]:
        """Acquire the per-request lifecycle lock with refcount tracking.

        Canonical entry point for serialising
        ``scope``/``approve``/``reject`` transitions on a request id.
        Bumps an in-flight refcount before returning the Lock so a
        concurrent eviction sweep (triggered when the registry hits
        ``_MAX_REQUEST_LOCKS``) cannot drop the entry between this
        method receiving the Lock and the body's implicit
        ``await lock.acquire()``. Without that gate, the next caller
        for the same id would mint a fresh Lock and two callers would
        end up holding *different* Lock objects for the same request,
        breaking the per-id ordering invariant.

        Mirrors the pattern in
        :mod:`synthorg.api.rate_limits.in_memory` (``_lock_refs``).
        """
        lock = self._reserve_request_lock(request_id)
        try:
            async with lock:
                yield
        finally:
            self._release_request_lock_ref(request_id)

    def _reserve_request_lock(self, request_id: str) -> asyncio.Lock:
        """Get-or-create the Lock and increment the in-flight refcount.

        Pairs with :meth:`_release_request_lock_ref`. Both operations
        execute under ``self._request_locks_guard`` so a concurrent
        eviction sweep observes the refcount bump and skips the entry.
        """
        with self._request_locks_guard:
            lock = self._request_locks.get(request_id)
            if lock is None:
                lock = asyncio.Lock()
                self._request_locks[request_id] = lock
                if len(self._request_locks) > _MAX_REQUEST_LOCKS:
                    self._evict_idle_request_locks_locked(_MAX_REQUEST_LOCKS)
            self._request_lock_refs[request_id] = (
                self._request_lock_refs.get(request_id, 0) + 1
            )
            return lock

    def _release_request_lock_ref(self, request_id: str) -> None:
        """Drop one in-flight reference to the per-request Lock.

        The refs entry is removed (rather than left at 0) once the
        count drops to zero so a quiescent id contributes nothing to
        memory.
        """
        with self._request_locks_guard:
            count = self._request_lock_refs.get(request_id, 0) - 1
            if count <= 0:
                self._request_lock_refs.pop(request_id, None)
            else:
                self._request_lock_refs[request_id] = count

    def _evict_idle_request_locks_locked(self, target_size: int) -> None:
        """Evict oldest idle entries down to ``target_size``.

        Caller must already hold ``self._request_locks_guard``. Iterates
        the OrderedDict in insertion order; entries whose Lock is held
        OR whose in-flight refcount is non-zero are kept, so a
        long-running scope still in flight (or one whose caller has
        just received the Lock but not yet entered ``async with``) is
        never stranded on an evicted Lock object.
        """
        # Snapshot keys before mutating the OrderedDict during iteration.
        for request_id in list(self._request_locks.keys()):
            if len(self._request_locks) <= target_size:
                return
            lock = self._request_locks[request_id]
            if not lock.locked() and self._request_lock_refs.get(request_id, 0) == 0:
                self._request_locks.pop(request_id, None)

    def release_request_lock_if_idle(self, request_id: str) -> None:
        """Drop the lock for ``request_id`` after a terminal transition.

        Called after the final ``save`` of a terminal state (approve,
        reject) so the registry does not accumulate one entry per
        lifetime request id. Only evicts when the lock is idle and
        no in-flight refcount remains -- a still-held or in-flight
        entry would strand a waiter who already holds a reference to
        the same :class:`asyncio.Lock` object. The caller must already
        have left the ``async with acquire_request_lock`` block (or
        directly released the Lock returned by
        :meth:`get_or_create_request_lock`) before invoking this
        helper, otherwise the ``locked()`` probe or refcount check
        reports the caller's own hold and the eviction is a no-op.
        """
        with self._request_locks_guard:
            lock = self._request_locks.get(request_id)
            if lock is None:
                return
            if lock.locked() or self._request_lock_refs.get(request_id, 0) > 0:
                # Caller violated the documented contract -- they're
                # still holding the lock when asking us to evict it.
                # Surface as DEBUG so the next reader of the logs can
                # find the caller bug; not WARN because the no-op is
                # safe (the registry just keeps the entry).
                logger.debug(
                    REQUEST_LOCK_RELEASE_SKIPPED_WHILE_HELD,
                    request_id=request_id,
                )
                return
            self._request_locks.pop(request_id, None)

    @property
    def ws_auth_timeout_seconds(self) -> float:
        """Return the WebSocket first-message auth-handshake timeout.

        Populated by ``_apply_bridge_config`` from
        ``api.ws_auth_timeout_seconds`` (``restart_required=True``, so the
        operator-visible contract is "takes effect at the next restart");
        always has a sane built-in default (10.0 s) so the handler
        never reaches back through the resolver per-connection.  The
        setter below is permissive by design -- tests and subsystems that
        need a different value at runtime may call it -- so the effective
        value is whichever ``set_ws_auth_timeout_seconds`` call ran most
        recently.
        """
        return self._ws_auth_timeout_seconds

    def set_ws_auth_timeout_seconds(self, value: float) -> None:
        """Store a validated WebSocket auth timeout on the app state.

        Mirrors the ``set_max_pending_per_user`` pattern used by the
        ticket store: ``_apply_bridge_config`` resolves the setting
        and calls this setter with the validated value at startup,
        which is then read by the ``/ws`` handler.  Repeated calls
        are allowed and the latest value wins -- tests monkeypatch
        this freely and no state in the mixin enforces a single-shot
        contract.  Bounds mirror the
        ``ApiBridgeConfig.ws_auth_timeout_seconds`` Pydantic field;
        the shared ``WS_AUTH_TIMEOUT_{MIN,MAX}_SECONDS`` constants
        keep the two sites aligned (DRY).
        """
        import math  # noqa: PLC0415

        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            WS_AUTH_TIMEOUT_MAX_SECONDS,
            WS_AUTH_TIMEOUT_MIN_SECONDS,
        )

        if not math.isfinite(value):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_auth_timeout_seconds",
                reason="non_finite",
                provided_value=repr(value),
            )
            msg = f"ws_auth_timeout_seconds must be finite, got {value!r}"
            raise ValueError(msg)
        if value < WS_AUTH_TIMEOUT_MIN_SECONDS or value > WS_AUTH_TIMEOUT_MAX_SECONDS:
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_auth_timeout_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=WS_AUTH_TIMEOUT_MIN_SECONDS,
                max_value=WS_AUTH_TIMEOUT_MAX_SECONDS,
            )
            msg = (
                "ws_auth_timeout_seconds must be between"
                f" {WS_AUTH_TIMEOUT_MIN_SECONDS} and"
                f" {WS_AUTH_TIMEOUT_MAX_SECONDS} seconds, got {value}"
            )
            raise ValueError(msg)
        self._ws_auth_timeout_seconds = value

    @property
    def ws_frame_timeout_seconds(self) -> int:
        """Per-frame WebSocket receive timeout in seconds.

        Bounded by ``[1, 600]``; defaults to 30. Read once at controller
        construction (read_only_post_init), so the value can be staged
        in tests via ``set_ws_frame_timeout_seconds`` without spinning
        the lifecycle.
        """
        return self._ws_frame_timeout_seconds

    def set_ws_frame_timeout_seconds(self, value: int) -> None:
        """Validate + cache the per-frame WebSocket idle timeout."""
        _reject_non_int(value, field="ws_frame_timeout_seconds")
        if not 1 <= value <= 600:  # noqa: PLR2004 -- bounds mirror Field(ge=1, le=600)
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_frame_timeout_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=1,
                max_value=600,
            )
            msg = (
                "ws_frame_timeout_seconds must be between 1 and"
                f" 600 seconds, got {value}"
            )
            raise ValueError(msg)
        self._ws_frame_timeout_seconds = value

    @property
    def ws_revalidation_window_seconds(self) -> int:
        """Sliding-window length for revalidation failure tracking."""
        return self._ws_revalidation_window_seconds

    def set_ws_revalidation_window_seconds(self, value: int) -> None:
        """Validate + cache the revalidation sliding-window length."""
        _reject_non_int(value, field="ws_revalidation_window_seconds")
        if not 1 <= value <= 3_600:  # noqa: PLR2004 -- bounds mirror Field(ge=1, le=3600)
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_revalidation_window_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=1,
                max_value=3_600,
            )
            msg = (
                "ws_revalidation_window_seconds must be between 1 and"
                f" 3600 seconds, got {value}"
            )
            raise ValueError(msg)
        self._ws_revalidation_window_seconds = value

    @property
    def ws_revalidation_max_failures(self) -> int:
        """Max revalidation failures admitted in the sliding window."""
        return self._ws_revalidation_max_failures

    def set_ws_revalidation_max_failures(self, value: int) -> None:
        """Validate + cache the revalidation max-failures cap."""
        _reject_non_int(value, field="ws_revalidation_max_failures")
        if not 1 <= value <= 100:  # noqa: PLR2004 -- bounds mirror Field(ge=1, le=100)
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_revalidation_max_failures",
                reason="out_of_range",
                provided_value=value,
                min_value=1,
                max_value=100,
            )
            msg = f"ws_revalidation_max_failures must be between 1 and 100, got {value}"
            raise ValueError(msg)
        self._ws_revalidation_max_failures = value

    @property
    def has_session_store(self) -> bool:
        """Check whether the session store is configured."""
        return self._session_store is not None

    @property
    def session_store(self) -> SessionStore:
        """Return the JWT session store."""
        return self._require_service(
            self._session_store,
            "session_store",
        )

    def set_session_store(self, store: SessionStore) -> None:
        """Attach the session store (once-only)."""
        self._set_once("_session_store", store, "Session store")

    @property
    def has_lockout_store(self) -> bool:
        """Check whether the lockout store is configured."""
        return self._lockout_store is not None

    @property
    def lockout_store(self) -> LockoutStore:
        """Return the account lockout store."""
        return self._require_service(
            self._lockout_store,
            "lockout_store",
        )

    def set_lockout_store(self, store: LockoutStore) -> None:
        """Attach the lockout store (once-only)."""
        self._set_once("_lockout_store", store, "Lockout store")

    @property
    def has_refresh_store(self) -> bool:
        """Check whether the refresh-token store is configured."""
        return self._refresh_store is not None

    @property
    def refresh_store(self) -> RefreshStore:
        """Return the refresh-token store."""
        return self._require_service(
            self._refresh_store,
            "refresh_store",
        )

    def set_refresh_store(self, store: RefreshStore) -> None:
        """Attach the refresh-token store (once-only)."""
        self._set_once("_refresh_store", store, "Refresh store")

    @property
    def user_presence(self) -> UserPresence:
        """Return the user presence tracker (always available)."""
        return self._user_presence

    def set_auth_service(self, service: AuthService) -> None:
        """Attach the auth service (once-only)."""
        self._set_once("_auth_service", service, "Auth service")

    @property
    def has_provider_registry(self) -> bool:
        """Check whether the provider registry is configured."""
        return self._provider_registry is not None

    @property
    def provider_registry(self) -> ProviderRegistry:
        """Return provider registry or raise 503."""
        return self._require_service(
            self._provider_registry,
            "provider_registry",
        )

    def swap_provider_registry(self, registry: ProviderRegistry) -> None:
        """Replace the provider registry (hot-reload)."""
        old_count = (
            len(self._provider_registry) if self._provider_registry is not None else 0
        )
        self._provider_registry = registry
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="provider_registry",
            old_provider_count=old_count,
            new_provider_count=len(registry),
        )

    @property
    def has_notification_dispatcher(self) -> bool:
        """Check whether the notification dispatcher is configured."""
        return self._notification_dispatcher is not None

    @property
    def notification_dispatcher(self) -> NotificationDispatcher:
        """Return notification dispatcher or raise 503."""
        return self._require_service(
            self._notification_dispatcher, "notification_dispatcher"
        )

    def swap_notification_dispatcher(
        self,
        dispatcher: NotificationDispatcher,
    ) -> NotificationDispatcher | None:
        """Swap the active notification dispatcher and return the prior one."""
        previous = self._notification_dispatcher
        self._notification_dispatcher = dispatcher
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="notification_dispatcher",
            old_id=id(previous) if previous is not None else None,
            new_id=id(dispatcher),
        )
        return previous

    @property
    def bridge_config_applied(self) -> bool:
        """Whether the API startup hook has applied bridge settings."""
        return self._bridge_config_applied

    def mark_bridge_config_applied(self) -> None:
        """Flip :attr:`bridge_config_applied` to ``True`` (one-way)."""
        self._bridge_config_applied = True

    @property
    def ontology_service(self) -> OntologyService:
        """Return ontology service or raise 503."""
        return self._require_service(
            self._ontology_service,
            "ontology_service",
        )

    @property
    def has_ontology_service(self) -> bool:
        """Check whether the ontology service is configured."""
        return self._ontology_service is not None

    @property
    def drift_report_store(self) -> DriftReportStore | None:
        """Return the drift report store, or None if not configured."""
        return self._drift_report_store

    @property
    def drift_detection_service(self) -> DriftDetectionService | None:
        """Return the drift detection service, or None if not configured."""
        return self._drift_detection_service

    @property
    def ontology_sync_service(self) -> OntologyOrgMemorySync | None:
        """Return the ontology sync service, or None if not configured."""
        return self._ontology_sync_service

    def set_drift_report_store(self, store: DriftReportStore) -> None:
        """Attach the drift report store (once-only)."""
        self._set_once("_drift_report_store", store, "Drift report store")

    def set_drift_detection_service(
        self,
        service: DriftDetectionService,
    ) -> None:
        """Attach the drift detection service (once-only)."""
        self._set_once(
            "_drift_detection_service",
            service,
            "Drift detection service",
        )

    def set_ontology_sync_service(
        self,
        service: OntologyOrgMemorySync,
    ) -> None:
        """Attach the ontology sync service (once-only)."""
        self._set_once(
            "_ontology_sync_service",
            service,
            "Ontology sync service",
        )

    @property
    def has_model_router(self) -> bool:
        """Check whether the model router is configured."""
        return self._model_router is not None

    @property
    def model_router(self) -> ModelRouter:
        """Return model router or raise 503."""
        return self._require_service(self._model_router, "model_router")

    def swap_model_router(self, router: ModelRouter) -> None:
        """Replace the model router (hot-reload)."""
        old_strategy = (
            self._model_router.strategy_name if self._model_router is not None else None
        )
        self._model_router = router
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="model_router",
            old_strategy=old_strategy,
            new_strategy=router.strategy_name,
        )

    @property
    def has_per_op_rate_limit_config(self) -> bool:
        """Check whether the per-op sliding-window config is set."""
        return self._per_op_rate_limit_config is not None

    @property
    def per_op_rate_limit_config(self) -> PerOpRateLimitConfig:
        """Return the current per-op sliding-window config or raise 503."""
        return self._require_service(
            self._per_op_rate_limit_config,
            "per_op_rate_limit_config",
        )

    def set_per_op_rate_limit_config(
        self,
        config: PerOpRateLimitConfig,
    ) -> None:
        """Attach the per-op sliding-window config at startup (once).

        Guards and middleware read through :attr:`per_op_rate_limit_config`
        at request time, so swapping this reference is how the settings
        subscriber applies runtime overrides without restarting the app.
        """
        self._per_op_rate_limit_config = config

    def swap_per_op_rate_limit_config(
        self,
        config: PerOpRateLimitConfig,
    ) -> None:
        """Replace the per-op sliding-window config (hot-reload).

        Called by the settings subscriber when operators change
        ``api.per_op_rate_limit_enabled`` or
        ``api.per_op_rate_limit_overrides``.  The store itself is not
        rebuilt -- only the config object swaps, so already-queued
        timestamps remain in place and a ``backend`` flip still needs
        a restart (it is marked ``restart_required=True``).
        """
        old_enabled = (
            self._per_op_rate_limit_config.enabled
            if self._per_op_rate_limit_config is not None
            else None
        )
        self._per_op_rate_limit_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="per_op_rate_limit_config",
            old_enabled=old_enabled,
            new_enabled=config.enabled,
            override_count=len(config.overrides),
        )

    @property
    def has_per_op_concurrency_config(self) -> bool:
        """Check whether the per-op inflight config is set."""
        return self._per_op_concurrency_config is not None

    @property
    def per_op_concurrency_config(self) -> PerOpConcurrencyConfig:
        """Return the current per-op inflight config or raise 503."""
        return self._require_service(
            self._per_op_concurrency_config,
            "per_op_concurrency_config",
        )

    def set_per_op_concurrency_config(
        self,
        config: PerOpConcurrencyConfig,
    ) -> None:
        """Attach the per-op inflight config at startup (once).

        Paired swap target for the inflight subscriber path; mirrors
        :meth:`set_per_op_rate_limit_config` so the two per-op guards
        have symmetric wiring.
        """
        self._per_op_concurrency_config = config

    def swap_per_op_concurrency_config(
        self,
        config: PerOpConcurrencyConfig,
    ) -> None:
        """Replace the per-op inflight config (hot-reload).

        Called by the settings subscriber on
        ``api.per_op_concurrency_enabled`` or
        ``api.per_op_concurrency_overrides`` change.  The inflight
        store keeps its counters -- only the enforcement config
        changes.
        """
        old_enabled = (
            self._per_op_concurrency_config.enabled
            if self._per_op_concurrency_config is not None
            else None
        )
        self._per_op_concurrency_config = config
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="per_op_concurrency_config",
            old_enabled=old_enabled,
            new_enabled=config.enabled,
            override_count=len(config.overrides),
        )

    @property
    def api_bridge_config(self) -> ApiBridgeConfig:
        """Return the current ``ApiBridgeConfig`` snapshot.

        Always non-None: ``__init__`` default-constructs an
        ``ApiBridgeConfig()`` so consumers see valid defaults even
        before ``_apply_bridge_config`` runs or when the resolver is
        unreachable.  Operator overrides land via
        :meth:`swap_api_bridge_config` from the startup snapshot path
        and the ``ApiBridgeSettingsSubscriber`` hot-reload path.
        """
        return self._api_bridge_config

    def swap_api_bridge_config(self, config: ApiBridgeConfig) -> None:
        """Replace the ``ApiBridgeConfig`` snapshot atomically.

        Called by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_api_bridge_config`` and by
        :class:`ApiBridgeSettingsSubscriber` on operator-driven changes
        to watched API settings.  A plain Python attribute assignment
        is atomic, so concurrent readers always see either the prior
        snapshot or the new one -- never a half-built object.
        """
        previous = self._api_bridge_config
        self._api_bridge_config = config
        if previous is config:
            return
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service="api_bridge_config",
            old_lifecycle_cap=previous.max_lifecycle_events_per_query,
            new_lifecycle_cap=config.max_lifecycle_events_per_query,
        )

    @property
    def has_backup_service(self) -> bool:
        """Check whether the backup service is configured."""
        return self._backup_service is not None

    @property
    def backup_service(self) -> BackupService:
        """Return backup service or raise 503."""
        return self._require_service(self._backup_service, "backup_service")

    def set_backup_service(self, service: BackupService) -> None:
        """Attach the backup service (once-only)."""
        self._set_once("_backup_service", service, "Backup service")

    @property
    def has_connection_catalog(self) -> bool:
        """Check whether the connection catalog is configured."""
        return self._connection_catalog is not None

    @property
    def connection_catalog(self) -> ConnectionCatalog:
        """Return connection catalog or raise 503."""
        return self._require_service(
            self._connection_catalog,
            "connection_catalog",
        )

    @property
    def has_tunnel_provider(self) -> bool:
        """Check whether the tunnel provider is configured."""
        return self._tunnel_provider is not None

    @property
    def tunnel_provider(self) -> TunnelProvider:
        """Return tunnel provider or raise 503."""
        return self._require_service(
            self._tunnel_provider,
            "tunnel_provider",
        )

    @property
    def oauth_token_manager(self) -> OAuthTokenManager | None:
        """Return OAuth token manager, or None if not configured."""
        return self._oauth_token_manager

    @property
    def has_oauth_state_service(self) -> bool:
        """Check whether the OAuth state service is configured."""
        return self._oauth_state_service is not None

    @property
    def oauth_state_service(self) -> OAuthStateService:
        """Return OAuth state service or raise 503.

        ``OAuthStateService`` is the audit-aware facade over
        ``persistence.oauth_states``; the OAuth controller routes its
        single ``save(...)`` write through this service so audit
        logging cannot regress.
        """
        return self._require_service(
            self._oauth_state_service,
            "oauth_state_service",
        )

    def set_oauth_state_service(self, service: OAuthStateService) -> None:
        """Attach the OAuth state service (once-only)."""
        self._set_once("_oauth_state_service", service, "OAuth state service")

    @property
    def health_prober_service(self) -> HealthProberService | None:
        """Return health prober service, or None if not configured."""
        return self._health_prober_service

    @property
    def webhook_event_bridge(self) -> WebhookEventBridge | None:
        """Return webhook event bridge, or None if not configured."""
        return self._webhook_event_bridge

    @property
    def escalation_store(self) -> EscalationQueueStore | None:
        """Return the escalation queue store, or None if not configured."""
        return self._escalation_store

    def set_escalation_store(self, store: EscalationQueueStore) -> None:
        """Attach the escalation queue store (once-only)."""
        self._set_once("_escalation_store", store, "escalation store")

    @property
    def escalation_registry(self) -> PendingFuturesRegistry | None:
        """Return the in-process futures registry, or None if not configured."""
        return self._escalation_registry

    def set_escalation_registry(self, registry: PendingFuturesRegistry) -> None:
        """Attach the escalation futures registry (once-only)."""
        self._set_once("_escalation_registry", registry, "escalation registry")

    @property
    def escalation_processor(self) -> DecisionProcessor | None:
        """Return the decision processor strategy, or None if not configured."""
        return self._escalation_processor

    def set_escalation_processor(self, processor: DecisionProcessor) -> None:
        """Attach the escalation decision processor (once-only)."""
        self._set_once("_escalation_processor", processor, "escalation processor")

    @property
    def escalation_sweeper(self) -> EscalationExpirationSweeper | None:
        """Return the background expiration sweeper, or None if not configured."""
        return self._escalation_sweeper

    def set_escalation_sweeper(self, sweeper: EscalationExpirationSweeper) -> None:
        """Attach the escalation expiration sweeper (once-only)."""
        self._set_once("_escalation_sweeper", sweeper, "escalation sweeper")

    @property
    def escalation_notify_subscriber(self) -> EscalationNotifySubscriber | None:
        """Return the cross-instance notify subscriber, or None if not configured."""
        return self._escalation_notify_subscriber

    def set_escalation_notify_subscriber(
        self,
        subscriber: EscalationNotifySubscriber,
    ) -> None:
        """Attach the cross-instance notify subscriber (once-only)."""
        self._set_once(
            "_escalation_notify_subscriber",
            subscriber,
            "escalation notify subscriber",
        )

    @property
    def a2a_card_builder(self) -> AgentCardBuilder:
        """Return the A2A Agent Card builder or raise 503."""
        return self._require_service(
            self._a2a_card_builder,
            "a2a_card_builder",
        )

    def set_a2a_card_builder(self, builder: AgentCardBuilder) -> None:
        """Attach the A2A card builder (once-only)."""
        self._set_once("_a2a_card_builder", builder, "A2A card builder")

    @property
    def a2a_client(self) -> A2AClient:
        """Return the outbound A2A client or raise 503."""
        return self._require_service(
            self._a2a_client,
            "a2a_client",
        )

    def set_a2a_client(self, client: A2AClient) -> None:
        """Attach the outbound A2A client (once-only)."""
        self._set_once("_a2a_client", client, "A2A client")

    @property
    def a2a_peer_registry(self) -> PeerRegistry:
        """Return the A2A peer registry or raise 503."""
        return self._require_service(
            self._a2a_peer_registry,
            "a2a_peer_registry",
        )

    def set_a2a_peer_registry(self, registry: PeerRegistry) -> None:
        """Attach the A2A peer registry (once-only)."""
        self._set_once(
            "_a2a_peer_registry",
            registry,
            "A2A peer registry",
        )

    @property
    def mcp_catalog_service(self) -> CatalogService:
        """Return MCP catalog service or raise 503."""
        return self._require_service(
            self._mcp_catalog_service,
            "mcp_catalog_service",
        )

    def set_mcp_catalog_service(self, service: CatalogService) -> None:
        """Attach the MCP catalog service (once-only)."""
        self._set_once("_mcp_catalog_service", service, "MCP catalog service")

    @property
    def has_mcp_installations_repo(self) -> bool:
        """Check whether the MCP installations repository is configured."""
        return self._mcp_installations_repo is not None

    @property
    def mcp_installations_repo(self) -> McpInstallationRepository:
        """Return the MCP installations repository or raise 503."""
        return self._require_service(
            self._mcp_installations_repo,
            "mcp_installations_repo",
        )

    def set_mcp_installations_repo(
        self,
        repo: McpInstallationRepository,
    ) -> None:
        """Attach the MCP installations repository (once-only)."""
        self._set_once(
            "_mcp_installations_repo",
            repo,
            "MCP installations repository",
        )

    # Facade-service accessors (signals / analytics / reports /
    # communication / META-MCP-2 phases 5-9) live on
    # :class:`~synthorg.api.state_services_facades._FacadesMixin`.

    def set_settings_service(self, settings_service: SettingsService) -> None:
        """Set settings service and rebuild derived services."""
        if self._settings_service is not None:
            logger.error(
                API_APP_STARTUP,
                action="service_already_configured",
                service="settings_service",
            )
            msg = "Settings service already configured"
            raise RuntimeError(msg)
        self._init_derived_services(
            settings_service=settings_service,
            config=self.config,
            persistence=self._persistence,
        )
        self._settings_service = settings_service
        logger.info(
            API_APP_STARTUP,
            action="service_configured",
            service="settings_service",
        )
