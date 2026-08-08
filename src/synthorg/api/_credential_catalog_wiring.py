# module-kind: code
"""Build the always-on credential catalog that backs LLM provider auth.

Separate from :mod:`synthorg.api.integrations_wiring` because the two have
opposite failure postures. The integrations feature surface (health prober,
OAuth, webhooks, rate-limit coordinator) is best-effort: an install without it
still runs. This catalog is not. Every provider that names a
``connection_name`` reads its key through it, so a boot that reaches serving
without one authenticates nothing, and the only symptom is the gateway's own
complaint about a missing environment variable on the first dispatch.
"""

from typing import ClassVar

from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.connection_protocol import ConnectionRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.secret_backends.protocol import SecretBackend

logger = get_logger(__name__)


class CredentialCatalogUnavailableError(DomainError):
    """The always-on credential catalog could not be built.

    Raised during boot construction, so no request is waiting on it; the
    posture is to refuse the boot rather than serve an org that can make no
    authenticated model call. It carries a code anyway because a boot failure
    an operator has to diagnose deserves the same identity as a request one.
    """

    default_message: ClassVar[str] = "Provider credential catalog is unavailable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.CREDENTIAL_CATALOG_UNAVAILABLE
    status_code: ClassVar[int] = 500


def build_provider_credential_catalog(
    *,
    effective_config: RootConfig,
    persistence: PersistenceBackend,
    db_url: str,
    secret_db_path: str | None,
) -> tuple[ConnectionCatalog, SecretBackend]:
    """Resolve the secret backend and build the credential catalog.

    Args:
        effective_config: The resolved root config, read for the secret-backend
            selection.
        persistence: The connected (or connecting) persistence backend.
        db_url: Postgres URL string; truthy means postgres mode.
        secret_db_path: The SQLite path for ``encrypted_sqlite``, already
            resolved by the caller.

    Returns:
        The catalog and the secret backend it reads through, so the caller can
        reuse the same backend for the secret-capture service.

    Raises:
        CredentialCatalogUnavailableError: The secret backend or the catalog
            could not be constructed. Fatal by design: provider auth has no
            other source, so booting past this serves an org that cannot make
            a single authenticated model call.
    """
    from synthorg.persistence.db_handle import postgres_pool_getter  # noqa: PLC0415
    from synthorg.persistence.secret_backends.factory import (  # noqa: PLC0415
        create_secret_backend,
        resolve_secret_backend_config,
    )

    postgres_mode = bool(db_url)
    pg_pool_getter = postgres_pool_getter(persistence) if postgres_mode else None
    try:
        selection = resolve_secret_backend_config(
            effective_config.integrations.secret_backend,
            postgres_mode=postgres_mode,
            pg_pool_available=pg_pool_getter is not None,
            sqlite_db_path=secret_db_path,
        )
        if selection.reason:
            log_fn = {
                "info": logger.info,
                "warning": logger.warning,
                "error": logger.error,
            }.get(selection.level, logger.info)
            log_fn(API_APP_STARTUP, note=selection.reason)
        secret_backend = create_secret_backend(
            selection.config,
            db_path=secret_db_path,
            pg_pool=pg_pool_getter,
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.error(
            API_APP_STARTUP,
            note="provider credential catalog could not be built",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"secret backend unavailable: {type(exc).__name__}"
        raise CredentialCatalogUnavailableError(msg) from exc
    return (
        ConnectionCatalog(
            repository=_connections_repository(persistence),
            secret_backend=secret_backend,
        ),
        secret_backend,
    )


def _connections_repository(persistence: PersistenceBackend) -> ConnectionRepository:
    """Return the connections repository, or an in-memory stand-in.

    ``auto_wire_integrations`` runs before ``persistence.connect()`` in the
    ``create_app`` boot path, and the durable connection repos require a live
    connection. The controllers still need an instance attached to the bundle
    so they register on the app and OpenAPI export sees them, so the wiring
    window gets the in-memory stub; the lifecycle hook rebuilds against the
    connected backend.

    Returns:
        The durable connections repository, or the in-memory stub.
    """
    try:
        return persistence.connections
    except PersistenceConnectionError:
        from synthorg.persistence.integration_inmemory import (  # noqa: PLC0415
            InMemoryConnectionRepository,
        )

        logger.warning(
            API_APP_STARTUP,
            note=(
                "persistence not yet connected; using in-memory "
                "connection repository for integrations wiring"
            ),
        )
        return InMemoryConnectionRepository()


__all__ = [
    "CredentialCatalogUnavailableError",
    "build_provider_credential_catalog",
]
