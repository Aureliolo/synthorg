# module-kind: orchestrator
"""Atomic persistence + hot-reload of provider configurations.

Extracted from :mod:`synthorg.providers.management.service` to keep that
module under its size budget. Holds the serialise / DB-write / in-memory
swap sequence and its rollback, each stage owning a distinct failure type
so the failing stage is unambiguous.
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
    # Validate: build registry + router before any I/O.
    try:
        registry = ProviderRegistry.from_config(new_providers)
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

    # Serialise into the versioned envelope (distinct from the DB write so
    # a serialise defect is not mistaken for a storage failure).
    # ``error=str(exc)`` / ``exc_info`` would risk leaking credential
    # material via exception text, so we redact and omit the traceback.
    try:
        serialized = serialize_provider_envelope(new_providers)
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

    # Snapshot the prior persisted state for rollback (parsed configs, plus
    # whether a DB row existed), then write the new blob.
    # ``providers.configs`` is a sensitive setting, so the raw
    # ``entry.value`` is ciphertext and not safe to re-set directly; the
    # parsed snapshot is re-serialised on rollback so the same
    # validate-then-encrypt write path runs.
    prior_entry = await settings_service.get_entry("providers", "configs")
    had_db_row = prior_entry.source == SettingSource.DATABASE
    prior_providers = dict(await config_resolver.get_provider_configs())
    try:
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

    # Hot-reload: swap in AppState (both sync, no await gap). On failure the
    # DB write is rolled back so the persisted blob and the running
    # registry stay consistent, and an ERROR alert fires.
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

    try:
        app_state.swap_provider_registry(registry)
        app_state.wire(ProvidersStateSlice, model_router=router)
    except Exception as exc:
        reraise_critical(exc)
        await _rollback_configs(
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
            rolled_back=True,
        )
        raise ProviderPersistenceError(msg) from exc


async def _rollback_configs(
    settings_service: SettingsService,
    *,
    had_db_row: bool,
    prior_providers: dict[str, ProviderConfig],
) -> None:
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
            PROVIDER_HOT_RELOAD_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            rollback_failed=True,
        )
