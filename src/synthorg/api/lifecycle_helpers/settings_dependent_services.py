# module-kind: code
"""Compose the settings-dependent services into their feature slices.

``ConfigResolver`` and the provider-management / org-mutation /
provider-audit / preset-override services all need a wired
``SettingsService`` (and, for the persistence-backed repos, a connected
backend). A single call wires every one of those downstream consumers'
slice fields, so callers run it both at construction (when a settings
service is injected) and at startup (when the settings service is
auto-wired) to keep them composed.
"""

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.management.audit_service import ProviderAuditService
from synthorg.providers.management.preset_override_service import (
    PresetOverrideService,
)
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import resolve_init_int
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


async def safe_compose_settings_dependent_services(
    app_state: AppState,
    settings_service: SettingsService | None,
    dispatcher: SettingsChangeDispatcher | None,
) -> None:
    """Run :func:`compose_settings_dependent_services` with redacted logging.

    Wrapper used by ``auto_wire_settings``: on success this is a thin
    pass-through; on failure it emits a redacted error log and stops
    the dispatcher (no leaked resources) before re-raising so the
    caller still aborts startup.
    """
    try:
        compose_settings_dependent_services(app_state, settings_service)
    except Exception as exc:
        # Propagate ``MemoryError`` / ``RecursionError`` unchanged before
        # any cleanup so resource-exhaustion failures surface to the
        # asyncio loop's exception handler rather than getting wrapped
        # in dispatcher-stop side effects (project convention).
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            note="Failed to compose settings-dependent services",
        )
        if dispatcher is not None:
            # Ordinary stop failures are best-effort (we're already on
            # the failure path); critical errors propagate so the
            # asyncio loop's handler still sees them.
            try:
                await dispatcher.stop()
            except Exception as stop_exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(stop_exc)
        raise


def compose_settings_dependent_services(
    app_state: AppState,
    settings_service: SettingsService | None,
) -> None:
    """Wire the settings service + config resolver + management services.

    No-op when *settings_service* is ``None`` (an empty / pre-settings
    boot): with no settings service there is nothing to resolve from.
    Otherwise wires *settings_service* onto its slice, builds a
    ``ConfigResolver`` from it + ``app_state.config``, and the
    provider-management, org-mutation, provider-audit, and
    preset-override services (the last two only when the backend exposes
    their repos), reading the persistence backend and cost tracker off
    their already-composed slices, then wires each service into its
    owning feature slice.

    Args:
        app_state: The application state composition root.
        settings_service: The wired settings service, or ``None``.
    """
    if settings_service is None:
        return

    app_state.wire(SettingsStateSlice, settings_service=settings_service)
    _wire_settings_read_facade(app_state, settings_service)
    config = app_state.config
    persistence = app_state.slice(PersistenceStateSlice).backend
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker

    resolver = ConfigResolver(settings_service=settings_service, config=config)
    audit_service, preset_override_service = _build_provider_audit_services(persistence)
    # Resolve the API bind port at bootstrap and inject it so the service reads
    # no env itself; ``resolve_init_int`` falls back to the registered default
    # on a non-integer value rather than raising at construction time.
    backend_port = resolve_init_int(SettingNamespace.API, "server_port")
    management = ProviderManagementService(
        settings_service=settings_service,
        config_resolver=resolver,
        app_state=app_state,
        config=config,
        backend_port=backend_port,
        audit_service=audit_service,
        cost_tracker=cost_tracker,
    )
    org_mutations = OrgMutationService(
        settings_service=settings_service,
        config_resolver=resolver,
        budget_config_versions=(
            persistence.budget_config_versions if persistence is not None else None
        ),
        company_versions=(
            persistence.company_versions if persistence is not None else None
        ),
        # Read per mutation, never captured: this runs before the roster is
        # published, and an operator granting a gate role expects the live
        # registry to know it now rather than at the next boot.
        agent_registry=lambda: app_state.slice(HrStateSlice).agent_registry,
    )
    app_state.wire(SettingsStateSlice, config_resolver=resolver)
    app_state.wire(
        ProvidersStateSlice,
        management=management,
        audit_service=audit_service,
        preset_override_service=preset_override_service,
    )
    app_state.wire(ApiCoreStateSlice, org_mutation_service=org_mutations)
    _wire_provider_read_facade(app_state, management)
    logger.info(
        API_APP_STARTUP,
        action="settings_dependent_services_wired",
        provider_audit=audit_service is not None,
        preset_override=preset_override_service is not None,
    )


def _build_provider_audit_services(
    persistence: PersistenceBackend | None,
) -> tuple[ProviderAuditService | None, PresetOverrideService | None]:
    """Build the provider-audit + preset-override services from the backend.

    Both are wired only when the connected backend exposes the backing repo,
    so a backend rig that lacks the accessor stays a no-op rather than
    erroring. Preset overrides emit an audit row on each write, so they are
    additionally absent whenever the audit service is absent.

    Returns:
        The ``(audit_service, preset_override_service)`` pair, each ``None``
        when its backing repo (or, for presets, the audit service) is absent.
    """
    provider_audit_repo = (
        getattr(persistence, "provider_audit_events", None)
        if persistence is not None
        else None
    )
    audit_service = (
        ProviderAuditService(provider_audit_repo)
        if provider_audit_repo is not None
        else None
    )
    preset_override_repo = (
        getattr(persistence, "preset_overrides", None)
        if persistence is not None
        else None
    )
    preset_override_service = (
        PresetOverrideService(preset_override_repo, audit_service=audit_service)
        if preset_override_repo is not None and audit_service is not None
        else None
    )
    return audit_service, preset_override_service


def _wire_settings_read_facade(
    app_state: AppState,
    settings_service: SettingsService,
) -> None:
    """Wire ``SettingsReadService`` onto the settings slice.

    The facade wraps the just-composed settings service so the
    ``synthorg_settings_*`` MCP tools resolve real data instead of 503-ing.
    Idempotent: skips when already wired so a re-compose at startup does not
    replace a live facade.
    """
    from synthorg.infrastructure.services import SettingsReadService  # noqa: PLC0415

    if app_state.slice(SettingsStateSlice).settings_read_service is not None:
        return
    app_state.wire(
        SettingsStateSlice,
        settings_read_service=SettingsReadService(settings=settings_service),
    )


def _wire_provider_read_facade(
    app_state: AppState,
    management: ProviderManagementService,
) -> None:
    """Wire ``ProviderReadService`` onto the facades slice.

    The provider registry + health tracker are construction-injected onto
    ``ProvidersStateSlice``, but the management service this facade also
    needs is only built here, so the read facade cannot wire at
    construction. Idempotent (skips when already wired) and a no-op when no
    provider is configured, so the provider MCP read tools stay 503 rather
    than projecting an empty registry.
    """
    from synthorg.infrastructure.services import ProviderReadService  # noqa: PLC0415
    from synthorg.infrastructure.state import FacadesStateSlice  # noqa: PLC0415

    providers = app_state.slice(ProvidersStateSlice)
    if (
        app_state.slice(FacadesStateSlice).provider_read_service is not None
        or providers.registry is None
        or providers.health_tracker is None
    ):
        return
    app_state.wire(
        FacadesStateSlice,
        provider_read_service=ProviderReadService(
            registry=providers.registry,
            health=providers.health_tracker,
            management=management,
        ),
    )
