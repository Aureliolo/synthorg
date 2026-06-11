"""Providers feature state slice.

Holds the provider registry, model router, health tracker, and the
management / audit / preset-override services. The registry, router,
and health tracker are constructor-injected at app build; the
management services are wired lazily once persistence is connected.
All fields are ``None`` until wired; readers guard accordingly.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.management.audit_service import (
    ProviderAuditService,
)
from synthorg.providers.management.preset_override_service import (
    PresetOverrideService,
)
from synthorg.providers.management.service import (
    ProviderManagementService,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.router import ModelRouter


class ProvidersStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the providers feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    registry: ProviderRegistry | None = None
    model_router: ModelRouter | None = None
    health_tracker: ProviderHealthTracker | None = None
    management: ProviderManagementService | None = None
    audit_service: ProviderAuditService | None = None
    preset_override_service: PresetOverrideService | None = None


def has_active_provider(app_state: AppStateSliceMixin) -> bool:
    """Report whether at least one LLM provider is registered.

    The single source of truth for the provider-present switch: the
    task-submission guard and the worker-execution-service builder both
    consult this so "empty company" means exactly the same thing in both
    places.

    Returns:
        ``True`` when the registry is wired and holds at least one provider.
    """
    registry = app_state.slice(ProvidersStateSlice).registry
    return registry is not None and len(registry) > 0


def provider_management_of(
    app_state: AppStateSliceMixin,
) -> ProviderManagementService:
    """Return the wired provider management service, or raise 503.

    The service lives on the providers state slice and is wired once
    persistence is connected. Provider mutation endpoints resolve it
    through this accessor so the slice lookup is centralised here; an
    unwired service surfaces a clean ``ServiceUnavailableError``.

    Args:
        app_state: The application state (any slice-reader).

    Returns:
        The wired provider management service.

    Raises:
        ServiceUnavailableError: When the service is not yet wired.
    """
    return require_service(
        app_state.slice(ProvidersStateSlice).management, "Provider Management"
    )


def provider_registry_of(app_state: AppStateSliceMixin) -> ProviderRegistry:
    """Return the wired provider registry, or raise 503.

    Returns:
        The wired provider registry.
    """
    return require_service(
        app_state.slice(ProvidersStateSlice).registry, "Provider Registry"
    )


def provider_health_tracker_of(app_state: AppStateSliceMixin) -> ProviderHealthTracker:
    """Return the wired provider health tracker, or raise 503.

    Returns:
        The wired provider health tracker.
    """
    return require_service(
        app_state.slice(ProvidersStateSlice).health_tracker,
        "Provider Health Tracker",
    )
