# module-kind: code
"""Service auto-wiring for production startup.

Re-exports the construction-time wiring (services that do not need a connected
persistence backend) from :mod:`synthorg.api.auto_wire_phase1` and
:mod:`synthorg.api.auto_wire_meetings`, and provides the on-startup wiring that
runs after persistence connects: ``auto_wire_settings`` (SettingsService +
dispatcher) and ``auto_wire_ontology``.
"""

from collections.abc import Callable

from synthorg.api.auto_wire_meetings import MeetingWireResult, auto_wire_meetings
from synthorg.api.auto_wire_phase1 import Phase1Result, auto_wire_phase1
from synthorg.api.state import AppState
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.ontology.service import OntologyService
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.service import SettingsService

__all__ = [
    "MeetingWireResult",
    "Phase1Result",
    "SettingsDispatcherBuilder",
    "auto_wire_meetings",
    "auto_wire_ontology",
    "auto_wire_phase1",
    "auto_wire_settings",
]

logger = get_logger(__name__)

type SettingsDispatcherBuilder = Callable[
    [
        MessageBus | None,
        SettingsService | None,
        RootConfig,
        AppState,
        BackupService | None,
        ApprovalTimeoutScheduler | None,
    ],
    SettingsChangeDispatcher | None,
]
"""Callable that builds the SettingsChangeDispatcher during on-startup wiring."""


async def auto_wire_settings(  # noqa: PLR0913
    persistence: PersistenceBackend,
    message_bus: MessageBus | None,
    effective_config: RootConfig,
    app_state: AppState,
    backup_service: BackupService | None,
    build_dispatcher: SettingsDispatcherBuilder,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None,
) -> SettingsChangeDispatcher | None:
    """On-startup auto-wire: create SettingsService after persistence connects.

    Called from ``on_startup`` after persistence connects. Creates the settings
    service, starts the dispatcher, and only then injects the service into
    *app_state* (to avoid partial state corruption if the dispatcher fails to
    start).

    Args:
        persistence: Connected persistence backend.
        message_bus: Message bus instance (may be ``None``).
        effective_config: Root company configuration.
        app_state: Application state container.
        backup_service: Backup service (for settings subscriber wiring).
        build_dispatcher: Callable that builds a settings dispatcher.
        approval_timeout_scheduler: Approval-timeout scheduler so the dispatcher
            can wire the matching subscriber for
            ``security.timeout_check_interval_seconds``.

    Returns:
        The started dispatcher, or ``None`` if ``build_dispatcher`` returns
        ``None`` (typically when no message bus is available).
    """
    # Deferred to break import cycle: settings.* -> api.* -> auto_wire
    import synthorg.settings.definitions  # noqa: F401, PLC0415
    from synthorg.settings.encryption import SettingsEncryptor  # noqa: PLC0415
    from synthorg.settings.registry import get_registry  # noqa: PLC0415
    from synthorg.settings.service import SettingsService  # noqa: PLC0415

    try:
        encryptor = SettingsEncryptor.from_env()
        settings_svc = SettingsService(
            repository=persistence.settings,
            registry=get_registry(),
            encryptor=encryptor,
            message_bus=message_bus,
        )
    except Exception as exc:
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            note="Failed to create SettingsService; check encryption key configuration",
        )
        raise

    # Build and start the dispatcher BEFORE mutating AppState, so a
    # dispatcher.start() failure doesn't leave app_state with a settings
    # service that has no running dispatcher.
    try:
        dispatcher = build_dispatcher(
            message_bus,
            settings_svc,
            effective_config,
            app_state,
            backup_service,
            approval_timeout_scheduler,
        )
    except Exception as exc:
        log_exception_redacted(
            logger, API_APP_STARTUP, exc, note="Failed to build settings dispatcher"
        )
        raise

    if dispatcher is not None:
        try:
            await dispatcher.start()
        except Exception as exc:
            log_exception_redacted(
                logger,
                API_APP_STARTUP,
                exc,
                note="Failed to start auto-wired settings dispatcher",
            )
            raise
        logger.info(API_SERVICE_AUTO_WIRED, service="settings_dispatcher")

    # All fallible operations succeeded -- safe to mutate AppState. The composer
    # wires the settings service onto its slice and the derived config-resolver
    # / management / org-mutation / audit / preset services; the safe wrapper
    # logs the failure with redaction and stops the dispatcher before
    # re-raising to prevent leaked tasks.
    from synthorg.api.lifecycle_helpers.settings_dependent_services import (  # noqa: PLC0415
        safe_compose_settings_dependent_services,
    )

    await safe_compose_settings_dependent_services(app_state, settings_svc, dispatcher)
    logger.info(API_SERVICE_AUTO_WIRED, service="settings_service")
    return dispatcher


async def auto_wire_ontology(
    effective_config: RootConfig,
    persistence: PersistenceBackend | None = None,
) -> OntologyService | None:
    """Auto-wire the ontology subsystem on top of the shared persistence backend.

    Wires versioning on top of ``persistence.get_db()``, constructs the
    ``OntologyService`` against ``persistence.ontology_entities``, and runs
    bootstrap (decorator registry + config entities).

    Args:
        effective_config: Root company configuration.
        persistence: Connected persistence backend (required). Safe to pass
            ``None`` -- the wire-up simply no-ops when the backend is absent.

    Returns:
        The bootstrapped ``OntologyService``, or ``None`` if wiring fails
        (non-fatal -- ontology is not required for startup).
    """
    from synthorg.observability.events.ontology import (  # noqa: PLC0415
        ONTOLOGY_AUTO_WIRE_FAILED,
    )
    from synthorg.ontology.service import OntologyService  # noqa: PLC0415

    ontology_config = effective_config.ontology
    if persistence is None or not getattr(persistence, "is_connected", False):
        logger.warning(
            ONTOLOGY_AUTO_WIRE_FAILED,
            error_type="NoPersistence",
            error="ontology auto-wire requires a connected persistence backend",
        )
        return None

    # Ontology runs on the shared persistence backend: the entity repository and
    # versioning service both take the backend's active DB handle, so schema
    # migrations and connection lifecycle are owned by PersistenceBackend.
    backend = persistence.ontology_entities
    try:
        versioning = persistence.build_ontology_versioning()
        service = OntologyService(
            backend=backend,
            versioning=versioning,
            config=ontology_config,
        )
        await service.bootstrap()
        if ontology_config.entities.entries:
            await service.bootstrap_from_config(ontology_config.entities)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            ONTOLOGY_AUTO_WIRE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    else:
        logger.info(API_SERVICE_AUTO_WIRED, service="ontology_service")
        return service
