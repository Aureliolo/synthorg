# module-kind: orchestrator
"""Atomic persistence + hot-reload of provider configurations.

Owns the serialise / DB-write / in-memory swap sequence and its rollback.
Each stage raises a distinct failure type so the failing stage is
unambiguous, and the hot-reload is made atomic with the DB write: a swap
failure rolls the persisted blob back so storage and the running registry
never diverge.
"""

from collections.abc import Callable

from synthorg.api.state import AppState
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CONFIG_PERSIST_FAILED,
    PROVIDER_CONFIG_SERIALIZE_FAILED,
    PROVIDER_HOT_RELOAD_FAILED,
    PROVIDER_RETRY_RESOLVE_FAILED,
    PROVIDER_VALIDATION_FAILED,
)
from synthorg.providers.errors import (
    ProviderPersistenceError,
    ProviderSerializationError,
    ProviderValidationError,
)
from synthorg.providers.management._helpers import serialize_provider_envelope
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.router import ModelRouter
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


async def apply_provider_change(
    *,
    app_state: AppState,
    settings_service: SettingsService,
    config_resolver: ConfigResolver,
    new_providers: dict[str, ProviderConfig],
    build_router: Callable[[dict[str, ProviderConfig]], ModelRouter],
) -> None:
    """Validate, serialise, persist, and hot-reload a provider set.

    The registry + router are built (validation) before any I/O; the
    hot-reload is then made atomic with the DB write: a swap failure rolls
    the persisted blob back to its prior value so the database and the
    in-memory registry never diverge.

    Args:
        app_state: Application state holding the live registry / router.
        settings_service: Settings store for the ``providers.configs`` blob.
        config_resolver: Resolver used to snapshot the prior parsed configs.
        new_providers: Complete new provider dict.
        build_router: Builds a ``ModelRouter`` from the provider dict.

    Raises:
        ProviderValidationError: If the registry/router build fails.
        ProviderSerializationError: If envelope serialisation fails.
        ProviderPersistenceError: If the DB write or the hot-reload (after
            rollback) fails.
    """
    retry_max_attempts = await resolve_retry_max_attempts(config_resolver)
    registry, router = _build_registry_and_router(
        new_providers, build_router, retry_max_attempts
    )
    serialized = _serialize_envelope(new_providers)
    had_db_row, prior_providers = await _snapshot_and_persist(
        settings_service=settings_service,
        config_resolver=config_resolver,
        serialized=serialized,
        new_providers=new_providers,
    )
    # Hot-reload: swap the registry and router in one atomic field-level
    # update so a partial swap cannot leave the registry new while the
    # router is stale. On failure the DB write is rolled back so the
    # persisted blob and the running registry stay consistent, and an ERROR
    # alert fires reporting whether the rollback actually restored storage.
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

    try:
        app_state.wire(ProvidersStateSlice, registry=registry, model_router=router)
    except Exception as exc:
        reraise_critical(exc)
        rolled_back = await _rollback_configs(
            settings_service,
            had_db_row=had_db_row,
            prior_providers=prior_providers,
        )
        msg = f"Provider hot-reload failed: {type(exc).__name__}"
        logger.error(
            PROVIDER_HOT_RELOAD_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            provider_count=len(new_providers),
            rolled_back=rolled_back,
        )
        raise ProviderPersistenceError(msg) from exc


async def resolve_retry_max_attempts(
    config_resolver: ConfigResolver,
) -> int | None:
    """Resolve ``providers.retry_max_attempts`` via the live resolver.

    Returns the org-wide retry cap so a registry rebuild re-applies the
    operator's setting instead of reverting to per-provider config defaults.
    Degrades to ``None`` (registry leaves each provider's own retry config
    untouched) on a corrupt value rather than blocking the provider change.

    Returns:
        The resolved retry cap, or ``None`` when the setting is unregistered
        (benign) or resolution fails on a corrupt value (logged WARNING).
    """
    try:
        return await config_resolver.get_int("providers", "retry_max_attempts")
    except SettingNotFoundError:
        # Setting not registered -- leave each provider's own retry untouched.
        return None
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_RETRY_RESOLVE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def _build_registry_and_router(
    new_providers: dict[str, ProviderConfig],
    build_router: Callable[[dict[str, ProviderConfig]], ModelRouter],
    retry_max_attempts: int | None = None,
) -> tuple[ProviderRegistry, ModelRouter]:
    """Build the registry + router (validation) before any I/O.

    Returns:
        The ``(registry, router)`` pair built from ``new_providers``.

    Raises:
        ProviderValidationError: If the registry/router build fails.
    """
    try:
        registry = ProviderRegistry.from_config(
            new_providers, retry_max_attempts=retry_max_attempts
        )
        router = build_router(new_providers)
    except Exception as exc:
        reraise_critical(exc)
        msg = f"Provider configuration validation failed: {type(exc).__name__}"
        logger.warning(
            PROVIDER_VALIDATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            provider_count=len(new_providers),
        )
        raise ProviderValidationError(msg) from exc
    return registry, router


def _serialize_envelope(new_providers: dict[str, ProviderConfig]) -> str:
    """Serialise the provider set into the versioned envelope.

    Kept distinct from the DB write so a serialise defect is not mistaken
    for a storage failure.

    Returns:
        The JSON-encoded versioned envelope string.

    Raises:
        ProviderSerializationError: If envelope serialisation fails.
    """
    try:
        return serialize_provider_envelope(new_providers)
    except Exception as exc:
        reraise_critical(exc)
        msg = f"Failed to serialise provider configuration: {type(exc).__name__}"
        logger.error(
            PROVIDER_CONFIG_SERIALIZE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            provider_count=len(new_providers),
        )
        raise ProviderSerializationError(msg) from exc


async def _snapshot_and_persist(
    *,
    settings_service: SettingsService,
    config_resolver: ConfigResolver,
    serialized: str,
    new_providers: dict[str, ProviderConfig],
) -> tuple[bool, dict[str, ProviderConfig]]:
    """Snapshot the prior persisted state, then write the new blob.

    The snapshot reads share the write's failure stage so a snapshot-read
    error maps to ``ProviderPersistenceError`` exactly like the write
    itself. ``providers.configs`` is a sensitive setting, so ``get_entry``
    returns a masked placeholder rather than the stored blob; the parsed
    snapshot is re-serialised on rollback so the same validate-then-encrypt
    write path runs.

    Returns:
        A ``(had_db_row, prior_providers)`` pair for the rollback path.

    Raises:
        ProviderPersistenceError: If a snapshot read or the DB write fails.
    """
    try:
        prior_entry = await settings_service.get_entry("providers", "configs")
        had_db_row = prior_entry.source == SettingSource.DATABASE
        prior_providers = dict(await config_resolver.get_provider_configs())
        await settings_service.set("providers", "configs", serialized)
    except Exception as exc:
        reraise_critical(exc)
        msg = f"Failed to persist provider configuration: {type(exc).__name__}"
        logger.error(
            PROVIDER_CONFIG_PERSIST_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            provider_count=len(new_providers),
        )
        raise ProviderPersistenceError(msg) from exc
    return had_db_row, prior_providers


async def _rollback_configs(
    settings_service: SettingsService,
    *,
    had_db_row: bool,
    prior_providers: dict[str, ProviderConfig],
) -> bool:
    """Restore ``providers.configs`` to its pre-write state.

    Re-serialises and persists the prior parsed configs (so the
    sensitive-setting encryption and validation run through the normal
    write path), or deletes the row when none existed before the write. A
    rollback failure is itself logged and swallowed (the original
    hot-reload failure is the one re-raised by the caller); masking the
    root cause with the rollback's own exception is worse for triage.

    Args:
        settings_service: Settings store for the ``providers.configs`` blob.
        had_db_row: Whether a ``providers.configs`` DB row existed before
            the failed write.
        prior_providers: The parsed provider configs to restore.

    Returns:
        ``True`` if storage was restored, ``False`` if the rollback write
        itself failed (so the caller's alert reports the true state rather
        than claiming a restore that did not happen).
    """
    try:
        if not had_db_row:
            await settings_service.delete("providers", "configs")
        else:
            await settings_service.set(
                "providers",
                "configs",
                serialize_provider_envelope(prior_providers),
            )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            PROVIDER_CONFIG_PERSIST_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            rollback_failed=True,
        )
        return False
    return True
