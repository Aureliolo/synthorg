"""Rebuild the provider registry from persisted provider configs.

Shared by the boot path (a restarted, already-set-up deployment must
come back with its providers live, exactly as agents are re-bootstrapped
in :mod:`synthorg.api.lifecycle_helpers.bootstrap`) and the
``/setup/complete`` reinit (which installs the first registry on an
empty-company boot).
"""

from synthorg.api.state import AppState
from synthorg.config.provider_configs_read import (
    ProviderConfigDiagnostics,
    ProviderConfigsRead,
    ProviderConfigsStatus,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.provider import (
    PROVIDER_CONFIG_ENTRY_REJECTED,
    PROVIDER_CONFIG_RETIRED_SETTING_STRIPPED,
)
from synthorg.providers._driver_binding import rebind_provider_set
from synthorg.providers.errors import ProviderConfigUnreadableError
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

_SEVERITY_BY_STATUS = {
    ProviderConfigsStatus.PARTIAL: NotificationSeverity.WARNING,
    ProviderConfigsStatus.UNREADABLE: NotificationSeverity.ERROR,
}


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
        (genuine first-run empty company). ``None`` means only that: a
        config that could not be read raises instead, so the two are never
        answered with the same value.

    Raises:
        ProviderConfigUnreadableError: When nothing usable could be read
            from the persisted config. An entry the current schema will
            not accept costs that entry alone; this is the case where
            none survived.
        Exception: Propagated from a failed config read or registry
            build; callers choose the failure posture (the boot step
            degrades to empty-company with a warning, the setup-complete
            reinit aborts completion).
    """
    from synthorg.integrations.state import (  # noqa: PLC0415
        provider_credential_catalog_of,
    )
    from synthorg.providers.management._persistence import (  # noqa: PLC0415
        resolve_retry_max_attempts,
    )

    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return None
    resolver = config_resolver_of(app_state)
    read = await resolver.get_provider_configs_read()
    # Recorded before the branch below, so the raise cannot leave the
    # deployment with a rejected config and nothing able to say so.
    app_state.wire(
        ProvidersStateSlice,
        config_diagnostics=ProviderConfigDiagnostics.of(read),
    )
    await _report_unusable_entries(app_state, read)
    if read.status is ProviderConfigsStatus.UNREADABLE:
        # Never the empty-company return below. That branch means "nobody
        # has configured a provider yet", and answering it here would hand
        # the operator a system that reports itself unconfigured while
        # their configuration sits intact in the database.
        raise ProviderConfigUnreadableError(_unreadable_message(read))
    provider_configs = read.providers
    if not provider_configs:
        return None
    retry_max_attempts = await resolve_retry_max_attempts(resolver)
    registry = ProviderRegistry.from_config(
        provider_configs,
        connection_catalog=provider_credential_catalog_of(app_state),
        retry_max_attempts=retry_max_attempts,
    )
    # This registry's drivers are new, so they report their completions
    # nowhere, and its ledger stamps against the set it replaced, until
    # both are pointed at this one.
    rebind_provider_set(app_state, registry, provider_configs, clock=app_state.clock)
    app_state.swap_provider_registry(registry)
    logger.info(
        API_APP_STARTUP,
        service="provider_registry",
        note="provider registry reloaded from persisted config",
        provider_count=len(provider_configs),
    )
    return registry


def _unreadable_message(read: ProviderConfigsRead) -> str:
    """Say what could not be read, in terms of what the operator configured.

    Two shapes reach here and they need different sentences. An envelope
    nothing could be made of has no entries to blame, so it carries its
    own detail. Entries that each failed on their own have no envelope
    detail, and naming them is the whole point: "unreadable" tells an
    operator nothing they can act on, "ollama, ollama-cloud" tells them
    where to look.

    Returns:
        The message to raise with.
    """
    if read.detail:
        return read.detail
    if read.rejected:
        named = ", ".join(rejected.name for rejected in read.rejected)
        return f"no persisted provider entry could be read: {named}"
    return "no persisted provider entry could be read"


async def reload_persisted_provider_registry_for_boot(
    app_state: AppState,
) -> ProviderRegistry | None:
    """Reload for the boot path, where an unreadable config must not stop it.

    The two callers of the reload want opposite things from the same
    failure. ``/setup/complete`` is an operator waiting on a request, so it
    gets the raise and a failed response. Boot is not: refusing to start
    would take away the dashboard, which is where the configuration gets
    corrected. So boot serves with no providers and says loudly why.

    Named and separate rather than inlined at the call site so the posture
    is a thing that can be tested, and so the two postures stay visibly
    different from each other.

    Args:
        app_state: Application state to reload the registry onto.

    Returns:
        The swapped-in registry, or ``None`` when there was nothing to
        load or the persisted config could not be read.
    """
    try:
        return await reload_persisted_provider_registry(app_state)
    except ProviderConfigUnreadableError as exc:
        # Deliberately not the empty-company phrasing the generic failure
        # path uses: this deployment HAS a configuration, and telling an
        # operator it is empty is the confusion the typed error ends.
        logger.error(
            API_APP_STARTUP,
            service="provider_registry",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="persisted provider config exists but could not be read;"
            " serving with no providers until it is corrected",
        )
        return None


async def _report_unusable_entries(
    app_state: AppState,
    read: ProviderConfigsRead,
) -> None:
    """Say what the persisted config lost or ignored on the way in.

    Coercions are logged and not notified. A retired setting is inert by
    definition and stripping it costs the operator nothing, but the blob
    keeps carrying it until they next edit that provider, so a
    notification would re-fire on every restart for a condition that never
    changes and train them to dismiss the channel. A rejected entry is the
    opposite: a connection they configured is not running.

    Args:
        app_state: Application state carrying the notification dispatcher.
        read: What the reader made of the persisted blob.
    """
    for coerced in read.coerced:
        logger.warning(
            PROVIDER_CONFIG_RETIRED_SETTING_STRIPPED,
            provider=coerced.name,
            setting=coerced.setting,
            note=(
                "the persisted config carries a retired setting; it is"
                " ignored, and the next edit of this provider drops it"
            ),
        )
    for rejected in read.rejected:
        logger.error(
            PROVIDER_CONFIG_ENTRY_REJECTED,
            provider=rejected.name,
            reason=rejected.reason,
        )
    severity = _SEVERITY_BY_STATUS.get(read.status)
    if severity is None:
        return
    await _notify(app_state, read, severity)


async def _notify(
    app_state: AppState,
    read: ProviderConfigsRead,
    severity: NotificationSeverity,
) -> None:
    """Raise one operator notification for an unusable persisted config.

    Best-effort: the caller either goes on to build a registry from what
    survived or raises, and neither outcome should hinge on a sink. The
    logs above have already recorded every condition.

    Raises:
        MemoryError: Re-raised via ``reraise_critical``.
        RecursionError: Re-raised via ``reraise_critical``.
    """
    dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
    if dispatcher is None:
        return
    named = ", ".join(rejected.name for rejected in read.rejected)
    body = (
        f"Provider connections that could not be read: {named}."
        if named
        else f"The persisted provider configuration could not be read: {read.detail}."
    )
    try:
        await dispatcher.dispatch(
            Notification(
                category=NotificationCategory.HEALTH,
                severity=severity,
                title="Persisted provider configuration could not be read",
                body=(
                    f"{body} They stay unavailable, and every feature bound to"
                    f" one is unwired, until the configuration is corrected in"
                    f" the dashboard."
                ),
                source="api.providers",
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the conditions are already logged above;
        # a sink fault must not decide whether the boot continues.
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="provider_registry",
            note="could not notify about the unreadable provider config",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
