"""Bridge-config and integration/escalation accessors for ``AppState``.

Extracted from ``state_services.py`` to keep each module under the
project's 800-line ceiling.  Hosts the per-op rate-limit / concurrency
config accessors, the ``Api``/``Workers``/``Memory`` bridge-config
snapshot accessors, and the backup / connection / tunnel / OAuth /
escalation / A2A / MCP service accessors.  Every accessor is a thin
pass-through to a private slot attribute on the concrete
:class:`AppState`; the mixin is combined into
:class:`~synthorg.api.state_services.AppStateServicesMixin` via
inheritance so the shared helpers (``_require_service``, ``_set_once``)
resolve at runtime.
"""

from typing import TYPE_CHECKING, Any

from synthorg.api.rate_limits.config import PerOpRateLimitConfig  # noqa: TC001
from synthorg.api.rate_limits.inflight_config import (
    PerOpConcurrencyConfig,  # noqa: TC001
)
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
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAPPED
from synthorg.settings.bridge_configs import (  # noqa: TC001
    ApiBridgeConfig,
    MemoryBridgeConfig,
    WorkersBridgeConfig,
)

if TYPE_CHECKING:
    import threading

    from pydantic import BaseModel

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

logger = get_logger(__name__)


class _BridgeIntegrationsMixin:
    """Mixin hosting bridge-config and integration accessors.

    Must be combined with the rest of ``AppStateServicesMixin`` via
    multiple inheritance so the shared helper methods
    (``_require_service``, ``_set_once``) resolve at runtime.
    """

    _set_once: Any

    def _require_service[T](  # pragma: no cover
        self, service: T | None, name: str
    ) -> T:
        """Return *service* or raise (implemented on concrete ``AppState``)."""
        raise NotImplementedError

    # Slot attrs the mixin reads directly (populated on concrete class).
    _per_op_rate_limit_config: PerOpRateLimitConfig | None
    _per_op_concurrency_config: PerOpConcurrencyConfig | None
    _api_bridge_config: ApiBridgeConfig
    _api_bridge_config_lock: threading.Lock
    _workers_bridge_config: WorkersBridgeConfig
    _workers_bridge_config_lock: threading.Lock
    _memory_bridge_config: MemoryBridgeConfig
    _memory_bridge_config_lock: threading.Lock
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
        and :meth:`mutate_api_bridge_config` from the
        ``ApiBridgeSettingsSubscriber`` hot-reload path.
        """
        return self._api_bridge_config

    def _swap_bridge_config(
        self,
        *,
        lock: threading.Lock,
        attr: str,
        service: str,
        config: BaseModel,
    ) -> None:
        """Replace a bridge-config snapshot wholesale under its lock.

        Shared body for the ``api`` / ``workers`` / ``memory`` swap
        accessors. Acquiring *lock* keeps a concurrent ``mutate_*``
        from interleaving its read with this assignment and losing the
        partial update.
        """
        with lock:
            previous: BaseModel = getattr(self, attr)
            setattr(self, attr, config)
        if previous is config:
            return
        prev_fields = previous.model_dump()
        new_fields = config.model_dump()
        changed = sorted(k for k in new_fields if prev_fields.get(k) != new_fields[k])
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service=service,
            transition="swap",
            changed_fields=changed,
        )

    def _mutate_bridge_config(
        self,
        *,
        lock: threading.Lock,
        attr: str,
        service: str,
        updates: dict[str, object],
    ) -> None:
        """Re-validate ``updates`` onto a bridge snapshot under its lock.

        Shared body for the ``api`` / ``workers`` / ``memory`` mutate
        accessors. Re-validation is forced via ``model_validate(...)``
        rather than ``model_copy(update=...)`` because Pydantic v2
        skips validators on the bare ``update=`` path -- an
        out-of-range operator value would otherwise land silently in
        the snapshot. Re-validation raises ``ValidationError``, leaving
        the prior snapshot in place and propagating the failure to the
        subscriber's error log. The whole read-modify-write runs inside
        *lock* so two concurrent operator edits cannot both build from
        the same prior value and lose each other's update.
        """
        with lock:
            previous: BaseModel = getattr(self, attr)
            merged = previous.model_dump()
            merged.update(updates)
            new_config = type(previous).model_validate(merged)
            setattr(self, attr, new_config)
        logger.info(
            SETTINGS_SERVICE_SWAPPED,
            service=service,
            transition="mutate",
            changed_fields=sorted(updates),
        )

    def swap_api_bridge_config(self, config: ApiBridgeConfig) -> None:
        """Replace the ``ApiBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_api_bridge_config`` (full
        snapshot, not a diff).  Hot-reload paths must use
        :meth:`mutate_api_bridge_config` instead so the read-modify-
        write is serialised against concurrent updates.

        Acquires ``_api_bridge_config_lock`` so a concurrent
        ``mutate_api_bridge_config`` cannot interleave its read with
        this assignment and lose the partial update.
        """
        self._swap_bridge_config(
            lock=self._api_bridge_config_lock,
            attr="_api_bridge_config",
            service="api_bridge_config",
            config=config,
        )

    def mutate_api_bridge_config(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the current snapshot under a lock.

        Combines a re-validating partial update and the swap into a
        single critical section so two concurrent operator edits cannot
        both build a new snapshot from the same prior value and lose
        each other's update.  The watched-key check in
        :class:`~synthorg.settings.subscribers.api_bridge_subscriber.ApiBridgeSettingsSubscriber`
        already restricts ``updates`` to fields declared on
        ``ApiBridgeConfig``.

        Re-validation is forced via ``model_validate(<dict>)`` rather
        than ``model_copy(update=...)`` because Pydantic v2 skips
        validators on the bare ``update=`` path -- an out-of-range
        operator-supplied value (e.g. ``50`` against
        ``Field(ge=100, le=1_000_000)``) would otherwise land silently
        in the snapshot.  Re-validation raises ``ValidationError``,
        leaving the prior snapshot in place and propagating the failure
        to the subscriber's error log.
        """
        self._mutate_bridge_config(
            lock=self._api_bridge_config_lock,
            attr="_api_bridge_config",
            service="api_bridge_config",
            updates=updates,
        )

    @property
    def workers_bridge_config(self) -> WorkersBridgeConfig:
        """Return the current ``WorkersBridgeConfig`` snapshot.

        Always non-None: ``__init__`` default-constructs a
        ``WorkersBridgeConfig()`` (Field defaults == the registered
        ``workers.*`` defaults) so a dispatcher built before
        ``_apply_bridge_config`` or under a resolver outage still
        observes the documented retry budget.
        """
        return self._workers_bridge_config

    def swap_workers_bridge_config(self, config: WorkersBridgeConfig) -> None:
        """Replace the ``WorkersBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_workers_bridge_config``.
        Hot-reload paths must use :meth:`mutate_workers_bridge_config`.
        """
        self._swap_bridge_config(
            lock=self._workers_bridge_config_lock,
            attr="_workers_bridge_config",
            service="workers_bridge_config",
            config=config,
        )

    def mutate_workers_bridge_config(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the workers snapshot under a lock.

        Re-validates via ``model_validate`` so an out-of-range operator
        value raises ``ValidationError`` and the prior snapshot is
        retained (mirrors :meth:`mutate_api_bridge_config`).
        """
        self._mutate_bridge_config(
            lock=self._workers_bridge_config_lock,
            attr="_workers_bridge_config",
            service="workers_bridge_config",
            updates=updates,
        )

    @property
    def memory_bridge_config(self) -> MemoryBridgeConfig:
        """Return the current ``MemoryBridgeConfig`` snapshot.

        Always non-None: ``__init__`` default-constructs a
        ``MemoryBridgeConfig()`` (Field defaults == the registered
        ``memory.*`` defaults) so a consumer built before
        ``_apply_bridge_config`` or under a resolver outage still
        observes the documented consolidation / fine-tune preflight
        defaults.
        """
        return self._memory_bridge_config

    def swap_memory_bridge_config(self, config: MemoryBridgeConfig) -> None:
        """Replace the ``MemoryBridgeConfig`` snapshot wholesale.

        Used by ``_apply_bridge_config`` at startup with the value
        resolved through ``ConfigResolver.get_memory_bridge_config``.
        Hot-reload paths must use :meth:`mutate_memory_bridge_config`.
        """
        self._swap_bridge_config(
            lock=self._memory_bridge_config_lock,
            attr="_memory_bridge_config",
            service="memory_bridge_config",
            config=config,
        )

    def mutate_memory_bridge_config(self, updates: dict[str, object]) -> None:
        """Apply ``updates`` to the memory snapshot under a lock.

        Re-validates via ``model_validate`` so an out-of-range operator
        value raises ``ValidationError`` and the prior snapshot is
        retained (mirrors :meth:`mutate_api_bridge_config`).
        """
        self._mutate_bridge_config(
            lock=self._memory_bridge_config_lock,
            attr="_memory_bridge_config",
            service="memory_bridge_config",
            updates=updates,
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
