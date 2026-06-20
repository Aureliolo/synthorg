# module-kind: orchestrator
"""One-time idempotent migration of embedded provider ``api_key`` to the catalog.

``ProviderConfig`` dropped its embedded ``api_key`` field in favour of a
``connection_name`` reference into the encrypted connection catalog. Installs
that already persisted an embedded key would, on upgrade, fail
``ProviderConfig`` validation and have the resolver silently fall back to an
empty provider map, losing every API-key provider.

This boot hook runs after persistence connects and before any normal provider
parse. It reads the raw ``providers.configs`` setting at the dict level (so the
old ``api_key`` is tolerated without going through the now-strict
``ProviderConfig`` schema), mints a catalog connection for each embedded key
via ``store_provider_api_key``, rewrites the config onto ``connection_name``,
and persists the migrated setting. It is idempotent: a config already on
``connection_name`` (or with no embedded key) is skipped, and a clean install
with nothing to migrate writes nothing back.

The API key value is never logged: only the provider name and a migrated count
are emitted, and the mint helper itself redacts.
"""

import json

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CREDENTIAL_MIGRATED,
    PROVIDER_CREDENTIAL_MIGRATION_COMPLETED,
    PROVIDER_CREDENTIAL_MIGRATION_FAILED,
)

logger = get_logger(__name__)

_NAMESPACE = "providers"
_KEY = "configs"


async def migrate_embedded_provider_keys(app_state: AppState) -> None:
    """Migrate any embedded provider ``api_key`` into the connection catalog.

    Best-effort + idempotent + persistence-gated. Never raises into the boot
    sequence and never logs the key.

    Args:
        app_state: The application state (provides settings + the catalog).
    """
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.settings.state import settings_service_of  # noqa: PLC0415

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    try:
        settings = settings_service_of(app_state)
        raw = (await settings.get(_NAMESPACE, _KEY)).value
        configs = json.loads(raw) if raw else {}
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_CREDENTIAL_MIGRATION_FAILED,
            phase="read",
            namespace=_NAMESPACE,
            key=_KEY,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    if not isinstance(configs, dict):
        return

    migrated, failed = await _migrate_configs(app_state, configs)
    if failed:
        # A failed mint leaves that provider's raw api_key in ``configs``.
        # Skip the write-back entirely so the plaintext key is never
        # persisted; the stored setting is left untouched and the next boot
        # retries the whole migration.
        logger.warning(
            PROVIDER_CREDENTIAL_MIGRATION_FAILED,
            phase="persist_skipped",
            migrated=migrated,
            failed=failed,
        )
        return
    if migrated == 0:
        return
    try:
        await settings.set(_NAMESPACE, _KEY, json.dumps(configs))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_CREDENTIAL_MIGRATION_FAILED,
            phase="persist",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(PROVIDER_CREDENTIAL_MIGRATION_COMPLETED, migrated=migrated)


async def _migrate_configs(
    app_state: AppState, configs: dict[str, object]
) -> tuple[int, int]:
    """Mint a catalog connection for each embedded key, rewriting in place.

    Returns:
        A ``(migrated, failed)`` pair: the number of provider configs
        migrated (an embedded key minted and rewritten onto
        ``connection_name``) and the number whose mint raised.
    """
    from synthorg.providers.management._credential_helpers import (  # noqa: PLC0415
        store_provider_api_key,
    )

    migrated = 0
    failed = 0
    for name, conf in configs.items():
        if not isinstance(conf, dict):
            continue
        api_key = conf.get("api_key")
        if not api_key or conf.get("connection_name"):
            # Nothing embedded, or already migrated onto a catalog connection.
            conf.pop("api_key", None)
            continue
        try:
            conn_name = await store_provider_api_key(app_state, name, str(api_key))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Leave the embedded key in place (do not pop): the caller skips
            # the write-back when any mint failed, so the plaintext is never
            # persisted and the next boot retries this provider.
            failed += 1
            logger.warning(
                PROVIDER_CREDENTIAL_MIGRATION_FAILED,
                phase="mint",
                provider=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        conf["connection_name"] = conn_name
        conf.pop("api_key", None)
        migrated += 1
        logger.info(PROVIDER_CREDENTIAL_MIGRATED, provider=name)
    return migrated, failed


__all__ = ["migrate_embedded_provider_keys"]
