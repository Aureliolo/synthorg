"""``SettingsChangeDispatcher`` assembly.

Glue helper that wires every ``SettingsSubscriber`` the API surface
needs into a single dispatcher. Subscribers are conditionally added
when their collaborator services are available (e.g. backup,
approval-timeout scheduler). The dispatcher is later swapped onto
``app_state`` via the lifecycle builder.
"""

from synthorg.api.state import AppState
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.observability import get_logger
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers import (
    A2AClientSettingsSubscriber,
    ApiBridgeSettingsSubscriber,
    ApiSecurityHeadersSettingsSubscriber,
    BackupSettingsSubscriber,
    BudgetBenchmarkProviderSettingsSubscriber,
    ChiefOfStaffAlertsSettingsSubscriber,
    EngineTimeoutEnforcementSettingsSubscriber,
    EscalationReconnectSettingsSubscriber,
    EventStreamHistorySettingsSubscriber,
    MemoryBridgeSettingsSubscriber,
    MemorySettingsSubscriber,
    MetaSelfImprovementSettingsSubscriber,
    NotificationsBridgeSettingsSubscriber,
    ObjectiveEntrySettingsSubscriber,
    ObservabilityBridgeSettingsSubscriber,
    ObservabilitySettingsSubscriber,
    PerOpRateLimitSettingsSubscriber,
    ProviderSettingsSubscriber,
    RuntimeReloadSettingsSubscriber,
    SecurityBridgeSettingsSubscriber,
    SecurityTimeoutSettingsSubscriber,
    ToolsBridgeSettingsSubscriber,
    WorkersBridgeSettingsSubscriber,
    WsAuthLimitsSettingsSubscriber,
)

logger = get_logger(__name__)


def _build_settings_dispatcher(  # noqa: PLR0913 -- one optional arg per subscriber the dispatcher carries
    message_bus: MessageBus | None,
    settings_service: SettingsService | None,
    config: RootConfig,
    app_state: AppState,
    backup_service: BackupService | None = None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None,
) -> SettingsChangeDispatcher | None:
    """Create settings change dispatcher if bus and settings are available.

    Returns:
        The ``SettingsChangeDispatcher`` value when present, ``None`` otherwise.
    """
    if message_bus is None or settings_service is None:
        return None
    provider_sub = ProviderSettingsSubscriber(
        config=config,
        app_state=app_state,
        settings_service=settings_service,
    )
    memory_sub = MemorySettingsSubscriber()
    log_dir = config.logging.log_dir if config.logging is not None else "logs"
    observability_sub = ObservabilitySettingsSubscriber(
        settings_service=settings_service,
        log_dir=log_dir,
    )
    per_op_rl_sub = PerOpRateLimitSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    api_bridge_sub = ApiBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    workers_bridge_sub = WorkersBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    memory_bridge_sub = MemoryBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    observability_bridge_sub = ObservabilityBridgeSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    meta_self_improvement_sub = MetaSelfImprovementSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    cos_alerts_sub = ChiefOfStaffAlertsSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    subs: list[SettingsSubscriber] = [
        provider_sub,
        memory_sub,
        observability_sub,
        per_op_rl_sub,
        api_bridge_sub,
        workers_bridge_sub,
        memory_bridge_sub,
        observability_bridge_sub,
        meta_self_improvement_sub,
        cos_alerts_sub,
        ApiSecurityHeadersSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        ObjectiveEntrySettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        ToolsBridgeSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        WsAuthLimitsSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        NotificationsBridgeSettingsSubscriber(
            app_state=app_state,
            config=config,
            settings_service=settings_service,
        ),
        RuntimeReloadSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        EngineTimeoutEnforcementSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        BudgetBenchmarkProviderSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        EventStreamHistorySettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        EscalationReconnectSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        A2AClientSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
        SecurityBridgeSettingsSubscriber(
            app_state=app_state,
            settings_service=settings_service,
        ),
    ]
    if backup_service is not None:
        subs.append(
            BackupSettingsSubscriber(
                backup_service=backup_service,
                settings_service=settings_service,
            ),
        )
    if approval_timeout_scheduler is not None:
        subs.append(
            SecurityTimeoutSettingsSubscriber(
                scheduler=approval_timeout_scheduler,
                settings_service=settings_service,
            ),
        )
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    return SettingsChangeDispatcher(
        message_bus=message_bus,
        subscribers=tuple(subs),
        config_resolver=config_resolver,
    )
