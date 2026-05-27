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

from typing import TYPE_CHECKING

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.budget.state import BudgetStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.management.audit_service import ProviderAuditService
from synthorg.providers.management.preset_override_service import (
    PresetOverrideService,
)
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


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
    config = app_state.config
    persistence = app_state.slice(PersistenceStateSlice).backend
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker

    resolver = ConfigResolver(settings_service=settings_service, config=config)
    # Provider audit log: wired only when the persistence backend actually
    # exposes the repo accessor, so a backend rig that lacks it stays a
    # no-op for emission rather than erroring.
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
    # Preset overrides depend on the audit service (they emit audit rows
    # on each write), so they are absent whenever audit is absent.
    preset_override_repo = (
        getattr(persistence, "preset_overrides", None)
        if persistence is not None
        else None
    )
    preset_override_service = (
        PresetOverrideService(preset_override_repo, audit_service=audit_service)
        if preset_override_repo is not None
        else None
    )
    management = ProviderManagementService(
        settings_service=settings_service,
        config_resolver=resolver,
        app_state=app_state,
        config=config,
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
    )
    app_state.wire(SettingsStateSlice, config_resolver=resolver)
    app_state.wire(
        ProvidersStateSlice,
        management=management,
        audit_service=audit_service,
        preset_override_service=preset_override_service,
    )
    app_state.wire(ApiCoreStateSlice, org_mutation_service=org_mutations)
    logger.info(
        API_APP_STARTUP,
        action="settings_dependent_services_wired",
        provider_audit=audit_service is not None,
        preset_override=preset_override_service is not None,
    )
