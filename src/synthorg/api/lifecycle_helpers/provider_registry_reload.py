"""Rebuild the provider registry from persisted provider configs.

Shared by the boot path (a restarted, already-set-up deployment must
come back with its providers live, exactly as agents are re-bootstrapped
in :mod:`synthorg.api.lifecycle_helpers.bootstrap`) and the
``/setup/complete`` reinit (which installs the first registry on an
empty-company boot).
"""

from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers._driver_binding import rebind_health_recorders
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)


async def reload_persisted_provider_registry(
    app_state: AppState,
) -> ProviderRegistry | None:
    """Build and swap in a registry from the persisted provider configs.

    Reads the DB-persisted provider set through the live resolver,
    rebuilds :class:`ProviderRegistry` with the credential catalogue
    bound and the org-wide retry cap applied, and hot-swaps it onto the
    providers slice.

    Returns:
        The swapped-in registry, or ``None`` when the resolver is not
        wired (anonymous / test boots) or no providers are persisted
        (genuine first-run empty company).

    Raises:
        Exception: Propagated from a failed config read or registry
            build; callers choose the failure posture (the boot step
            degrades to empty-company with a warning, the setup-complete
            reinit aborts completion).
    """
    from synthorg.integrations.state import (  # noqa: PLC0415
        provider_credential_catalog_of,
    )
    from synthorg.providers.management._persistence import (  # noqa: PLC0415
        resolve_default_provider_name,
        resolve_retry_max_attempts,
    )

    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return None
    resolver = config_resolver_of(app_state)
    provider_configs = await resolver.get_provider_configs()
    if not provider_configs:
        return None
    retry_max_attempts = await resolve_retry_max_attempts(resolver)
    registry = ProviderRegistry.from_config(
        provider_configs,
        connection_catalog=provider_credential_catalog_of(app_state),
        retry_max_attempts=retry_max_attempts,
    )
    registry.bind_default_provider(await resolve_default_provider_name(resolver))
    # This registry's drivers are new, so they report their completions
    # nowhere until they are pointed at the tracker.
    rebind_health_recorders(app_state, registry)
    app_state.swap_provider_registry(registry)
    logger.info(
        API_APP_STARTUP,
        service="provider_registry",
        note="provider registry reloaded from persisted config",
        provider_count=len(provider_configs),
    )
    return registry
