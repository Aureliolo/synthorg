"""Secret backend factory.

Creates a ``SecretBackend`` instance from configuration.
"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.integrations.config import SecretBackendConfig  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    SECRET_BACKEND_UNAVAILABLE,
)
from synthorg.persistence.secret_backends.encrypted_postgres import (
    EncryptedPostgresSecretBackend,
)
from synthorg.persistence.secret_backends.encrypted_sqlite import (
    EncryptedSqliteSecretBackend,
)
from synthorg.persistence.secret_backends.env_var import (
    EnvVarSecretBackend,
)
from synthorg.persistence.secret_backends.protocol import (
    SecretBackend,  # noqa: TC001
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from psycopg_pool import AsyncConnectionPool


logger = get_logger(__name__)


@dataclass(frozen=True)
class SecretBackendSelection:
    """Result of :func:`resolve_secret_backend_config`.

    Attributes:
        config: The (possibly rewritten) ``SecretBackendConfig`` with
            the resolved ``backend_type``.
        reason: Human-readable explanation of any auto-selection or
            downgrade that happened, or empty string if the config
            was honoured as-is.
        level: Log level appropriate for ``reason`` -- ``"info"`` for
            silent honour-as-is, ``"warning"`` for a benign promotion
            (sqlite -> postgres in postgres mode), ``"error"`` for a
            security-relevant downgrade (missing master key, missing
            store). Callers log the reason at this level.
    """

    config: SecretBackendConfig
    reason: str
    level: str


def _resolve_backend_type(
    config: SecretBackendConfig,
    *,
    postgres_mode: bool,
    pg_pool_available: bool,
    sqlite_db_path: str | None,
) -> tuple[str, str, str]:
    """Pick the backend type, reason, and log level for this environment."""
    resolved = config.backend_type
    if resolved == "encrypted_sqlite" and postgres_mode:
        if pg_pool_available:
            return (
                "encrypted_postgres",
                (
                    "default encrypted_sqlite promoted to encrypted_postgres "
                    "to match Postgres persistence"
                ),
                "warning",
            )
        return (
            "env_var",
            (
                "encrypted secret backend requested in postgres mode but "
                "no pool is available; falling back to env_var"
            ),
            "error",
        )
    if resolved == "encrypted_sqlite" and sqlite_db_path is None:
        return (
            "env_var",
            "encrypted_sqlite secret backend has no db_path; falling back to env_var",
            "error",
        )
    if resolved == "encrypted_postgres" and not pg_pool_available:
        return (
            "env_var",
            (
                "encrypted_postgres secret backend has no pg_pool; "
                "falling back to env_var"
            ),
            "error",
        )
    return resolved, "", "info"


def _check_master_key(
    resolved: str,
    config: SecretBackendConfig,
) -> tuple[str, str, str] | None:
    """Verify the master-key env var is set for encrypted backends.

    Returns a ``(resolved, reason, level)`` override if the key is
    missing (forcing a downgrade to ``env_var``); returns ``None`` when
    the backend is not encrypted or the key is present.
    """
    if resolved not in ("encrypted_sqlite", "encrypted_postgres"):
        return None
    master_key_env = (
        config.encrypted_sqlite.master_key_env
        if resolved == "encrypted_sqlite"
        else config.encrypted_postgres.master_key_env
    )
    if os.environ.get(master_key_env, "").strip():
        return None
    return (
        "env_var",
        (
            f"{master_key_env} is not set; encrypted secret backend "
            "requires a Fernet key. Falling back to env_var (no "
            f"at-rest encryption). Set {master_key_env} (URL-safe "
            "base64 of 32 bytes) to enable encrypted secret storage."
        ),
        "error",
    )


def _promote_to_postgres(config: SecretBackendConfig) -> SecretBackendConfig:
    """Rewrite ``config`` for the sqlite-to-postgres auto-promotion.

    Carries the operator-specified ``encrypted_sqlite.master_key_env`` into
    ``encrypted_postgres.master_key_env`` so a customised env var isn't lost
    during the auto-promotion. Without this the promoted backend would look
    up a different env var (the postgres default) and silently fall back to
    ``env_var`` even though the operator's key is set.
    """
    sqlite_env = config.encrypted_sqlite.master_key_env
    postgres_cfg = config.encrypted_postgres.model_copy(
        update={"master_key_env": sqlite_env}
    )
    return config.model_copy(
        update={
            "backend_type": "encrypted_postgres",
            "encrypted_postgres": postgres_cfg,
        }
    )


def resolve_secret_backend_config(
    config: SecretBackendConfig,
    *,
    postgres_mode: bool,
    pg_pool_available: bool,
    sqlite_db_path: str | None,
) -> SecretBackendSelection:
    """Auto-select the correct secret backend for the active persistence.

    Selection rules (checked top to bottom):

    1. Default ``encrypted_sqlite`` + postgres mode + live pool
       -> promote to ``encrypted_postgres`` (benign, WARNING).
    2. Default ``encrypted_sqlite`` + postgres mode + no pool ->
       downgrade to ``env_var`` (ERROR: integrations degraded).
    3. ``encrypted_sqlite`` with no db_path (sqlite mode without
       SYNTHORG_DB_PATH) -> downgrade to ``env_var`` (ERROR).
    4. Explicit ``encrypted_postgres`` without a live pool ->
       downgrade to ``env_var`` (ERROR).
    5. Any ``encrypted_*`` with no ``SYNTHORG_MASTER_KEY`` env var ->
       downgrade to ``env_var`` (ERROR: no at-rest encryption).
    6. Otherwise honour the config as-is.

    Args:
        config: Configured secret backend.
        postgres_mode: Whether the active persistence backend is
            Postgres (vs SQLite).
        pg_pool_available: Whether the Postgres pool can be produced on
            demand. Callers may pass ``True`` when they hold a lazy
            getter that resolves at first use (the pool is then
            acquired after ``persistence.connect()`` completes).
        sqlite_db_path: SQLite DB path if available, else ``None``.

    Returns:
        A :class:`SecretBackendSelection` describing the resolved
        config plus a human-readable reason (empty if unchanged).
    """
    resolved, reason, level = _resolve_backend_type(
        config,
        postgres_mode=postgres_mode,
        pg_pool_available=pg_pool_available,
        sqlite_db_path=sqlite_db_path,
    )

    # Apply the sqlite-to-postgres promotion before the master-key
    # check so the check uses the already-propagated master_key_env
    # (see _promote_to_postgres). Without this ordering a custom
    # encrypted_sqlite.master_key_env would be lost during the
    # env-var lookup and the backend would spuriously downgrade.
    if resolved == "encrypted_postgres" and config.backend_type == "encrypted_sqlite":
        resolved_config = _promote_to_postgres(config)
    elif resolved == config.backend_type:
        resolved_config = config
    else:
        resolved_config = config.model_copy(update={"backend_type": resolved})

    key_override = _check_master_key(resolved, resolved_config)
    if key_override is not None:
        resolved, reason, level = key_override
        resolved_config = resolved_config.model_copy(update={"backend_type": resolved})

    return SecretBackendSelection(config=resolved_config, reason=reason, level=level)


def _build_encrypted_sqlite(
    config: SecretBackendConfig,
    *,
    db_path: str | None = None,
    **_unused: object,
) -> SecretBackend:
    if db_path is None:
        logger.error(
            SECRET_BACKEND_UNAVAILABLE,
            backend="encrypted_sqlite",
            error="db_path is required for encrypted_sqlite",
        )
        msg = "db_path is required for encrypted_sqlite secret backend"
        raise ValueError(msg)
    return EncryptedSqliteSecretBackend(
        db_path=db_path,
        config=config.encrypted_sqlite,
    )


def _build_encrypted_postgres(
    config: SecretBackendConfig,
    *,
    pg_pool: "AsyncConnectionPool | Callable[[], AsyncConnectionPool] | None" = None,  # noqa: UP037
    **_unused: object,
) -> SecretBackend:
    if pg_pool is None:
        logger.error(
            SECRET_BACKEND_UNAVAILABLE,
            backend="encrypted_postgres",
            error="pg_pool is required for encrypted_postgres",
        )
        msg = "pg_pool is required for encrypted_postgres secret backend"
        raise ValueError(msg)
    return EncryptedPostgresSecretBackend(
        pool=pg_pool,
        config=config.encrypted_postgres,
    )


def _build_env_var(
    config: SecretBackendConfig,
    **_unused: object,
) -> SecretBackend:
    return EnvVarSecretBackend(config=config.env_var)


_SECRET_BACKEND_REGISTRY: StrategyRegistry[SecretBackend] = StrategyRegistry(
    {
        "encrypted_sqlite": _build_encrypted_sqlite,
        "encrypted_postgres": _build_encrypted_postgres,
        "env_var": _build_env_var,
    },
    kind="secret_backend",
)


def create_secret_backend(
    config: SecretBackendConfig,
    *,
    db_path: str | None = None,
    pg_pool: "AsyncConnectionPool | Callable[[], AsyncConnectionPool] | None" = None,  # noqa: UP037
) -> SecretBackend:
    """Create a secret backend from configuration.

    Args:
        config: Secret backend configuration.
        db_path: SQLite database path (required for
            ``encrypted_sqlite``).
        pg_pool: Async Postgres connection pool or a zero-arg callable
            that returns one. A callable defers pool acquisition to
            the first operation, which lets ``create_app`` wire the
            backend before ``persistence.connect()`` has run. Required
            for ``encrypted_postgres``.

    Returns:
        A configured ``SecretBackend`` instance.

    Raises:
        ValueError: If the backend type is misconfigured (missing
            ``db_path`` for ``encrypted_sqlite`` or missing
            ``pg_pool`` for ``encrypted_postgres``).
        StrategyFactoryNotFoundError: If ``config.backend_type`` is
            outside the registered set (normally unreachable thanks
            to the ``Literal`` union on the config field).
    """
    return _SECRET_BACKEND_REGISTRY.build(
        config.backend_type,
        config,
        db_path=db_path,
        pg_pool=pg_pool,
    )
