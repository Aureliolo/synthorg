"""``SettingsChangeDispatcher`` assembly.

Glue helper that wires every ``SettingsSubscriber`` the API surface
needs into a single dispatcher. Subscribers are conditionally added
when their collaborator services are available (e.g. backup,
approval-timeout scheduler). The dispatcher is later swapped onto
``app_state`` via the lifecycle builder.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.subscribers import (
    ApiBridgeSettingsSubscriber,
    BackupSettingsSubscriber,
    MemoryBridgeSettingsSubscriber,
    MemorySettingsSubscriber,
    ObservabilitySettingsSubscriber,
    PerOpRateLimitSettingsSubscriber,
    ProviderSettingsSubscriber,
    SecurityTimeoutSettingsSubscriber,
    WorkersBridgeSettingsSubscriber,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.backup.service import BackupService
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.config.schema import RootConfig
    from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
    from synthorg.settings.service import SettingsService
    from synthorg.settings.subscriber import SettingsSubscriber

logger = get_logger(__name__)


def _build_settings_dispatcher(  # noqa: PLR0913 -- one optional arg per subscriber the dispatcher carries
    message_bus: MessageBus | None,
    settings_service: SettingsService | None,
    config: RootConfig,
    app_state: AppState,
    backup_service: BackupService | None = None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None,
) -> SettingsChangeDispatcher | None:
    """Create settings change dispatcher if bus and settings are available."""
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
    subs: list[SettingsSubscriber] = [
        provider_sub,
        memory_sub,
        observability_sub,
        per_op_rl_sub,
        api_bridge_sub,
        workers_bridge_sub,
        memory_bridge_sub,
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
    config_resolver = (
        app_state.config_resolver if app_state.has_config_resolver else None
    )
    return SettingsChangeDispatcher(
        message_bus=message_bus,
        subscribers=tuple(subs),
        config_resolver=config_resolver,
    )
