"""Public factory helpers for building persistence configuration.

High-level packages (``api/``, ``engine/``, ``communication/``) build
``PersistenceConfig`` and ``ArtifactStorageBackend`` instances through
these helpers so they never have to import ``SQLiteConfig`` /
``PostgresConfig`` / ``FileSystemArtifactStorage`` directly. The
dependency-inversion gate flags any non-factory module under those
packages that does.

The CLI compose template emits exactly one of
``SYNTHORG_DATABASE_URL`` (Postgres) or ``SYNTHORG_DB_PATH`` (SQLite)
per init choice; the helpers below cover both shapes.
"""

from pathlib import Path  # noqa: TC003 -- runtime-resolvable annotation for PEP 649
from typing import Any, NoReturn, get_args
from urllib.parse import unquote, urlparse

from pydantic import SecretStr

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.artifact_storage import (
    ArtifactStorageBackend,  # noqa: TC001 -- documented return type
)
from synthorg.persistence.config import (
    PersistenceConfig,
    PostgresConfig,
    PostgresSslMode,
    SQLiteConfig,
)
from synthorg.persistence.filesystem_artifact_storage import (
    FileSystemArtifactStorage,
)

logger = get_logger(__name__)


def build_sqlite_persistence_config(*, path: str) -> PersistenceConfig:
    """Build a ``PersistenceConfig`` for a SQLite database at *path*.

    Callers (typically ``api/app.py`` auto-wire) get back the abstract
    ``PersistenceConfig`` envelope so they never have to import the
    concrete ``SQLiteConfig`` class directly.

    Returns:
        Result of type ``PersistenceConfig``.
    """
    return PersistenceConfig(
        backend="sqlite",
        sqlite=SQLiteConfig(path=path),
    )


def _fail_url(
    msg: str,
    reason: str,
    cause: Exception | None = None,
) -> NoReturn:
    """Log and raise for a Postgres-URL configuration failure.

    Raises:
        ValueError: If an argument fails validation.
    """
    logger.warning(API_APP_STARTUP, error=msg, reason=reason)
    raise ValueError(msg) from cause


def _validate_postgres_url(db_url: str) -> tuple[str, int, str, str, str]:
    """Parse and validate *db_url*; return ``(host, port, db, user, pass)``.

    Each component is URL-decoded before return so credentials with
    reserved characters survive the round-trip. The port is resolved
    via :func:`_resolve_postgres_port` so an explicit ``:0`` is rejected
    rather than silently rewritten to ``5432``.

    Raises:
        ValueError (via :func:`_fail_url`) on any structural problem
        (parse failure, query parameters, wrong scheme, missing
        host/credentials/database, port 0).

    Returns:
        The matching collection.
    """
    try:
        parsed = urlparse(db_url)
    except ValueError as exc:
        _fail_url(
            f"SYNTHORG_DATABASE_URL could not be parsed: {safe_error_description(exc)}",
            "url_parse_failed",
            exc,
        )
    if parsed.query:
        _fail_url(
            "SYNTHORG_DATABASE_URL must not include query parameters; use "
            "SYNTHORG_POSTGRES_SSL_MODE for ssl_mode overrides",
            "unsupported_query_params",
        )
    if parsed.scheme not in {"postgres", "postgresql"}:
        _fail_url(
            f"SYNTHORG_DATABASE_URL scheme {parsed.scheme!r} is not "
            f"supported; expected 'postgresql://...'",
            "invalid_scheme",
        )
    try:
        hostname = parsed.hostname
        parsed_port = parsed.port
    except ValueError as exc:
        _fail_url(
            f"SYNTHORG_DATABASE_URL has an invalid host/port: {safe_error_description(exc)}",  # noqa: E501
            "invalid_host_port",
            exc,
        )
    if not hostname:
        _fail_url("SYNTHORG_DATABASE_URL is missing a host component", "missing_host")
    if not parsed.username or not parsed.password:
        _fail_url(
            "SYNTHORG_DATABASE_URL must include a username and password "
            "(postgresql://user:pass@host:port/db)",
            "missing_credentials",
        )
    database = parsed.path.lstrip("/")
    if not database:
        _fail_url(
            "SYNTHORG_DATABASE_URL must include a database name in the "
            "path (postgresql://user:pass@host:port/db)",
            "missing_database",
        )
    port = _resolve_postgres_port(parsed_port)
    return (
        unquote(hostname),
        port,
        unquote(database),
        unquote(parsed.username),
        unquote(parsed.password),
    )


def _resolve_postgres_port(parsed_port: int | None) -> int:
    """Return the effective Postgres port, rejecting an explicit ``:0``.

    ``urllib.parse`` returns ``None`` when the URL has no port and ``0``
    when the URL contains an explicit ``:0``. The previous
    ``parsed_port or 5432`` fallback collapsed both into ``5432`` and
    silently masked an operator misconfiguration, sending startup at
    the default port instead of failing fast. Distinguishing the two
    cases keeps the default-port convenience for ``...@host/db`` while
    surfacing ``...@host:0/db`` as a configuration error.

    Returns:
        Numeric result of the operation.
    """
    if parsed_port is None:
        return 5432
    if parsed_port == 0:
        _fail_url(
            "SYNTHORG_DATABASE_URL has port 0; use a positive port or "
            "omit the port to default to 5432",
            "invalid_port_zero",
        )
    return parsed_port


def _normalize_ssl_mode_kwargs(ssl_mode_override: str | None) -> dict[str, Any]:
    """Validate ``ssl_mode_override`` and return ``PostgresConfig`` kwargs.

    Empty / ``None`` overrides return an empty dict so the caller's
    ``**ssl_kwargs`` spread leaves the :class:`PostgresConfig` default
    (``"require"``) in place.

    Returns:
        Result of type ``dict[str, Any]``.
    """
    if not ssl_mode_override:
        return {}
    valid_modes = set(get_args(PostgresSslMode))
    if ssl_mode_override not in valid_modes:
        _fail_url(
            f"SYNTHORG_POSTGRES_SSL_MODE={ssl_mode_override!r} is invalid; "
            f"must be one of: {sorted(valid_modes)}",
            "invalid_ssl_mode",
        )
    return {"ssl_mode": ssl_mode_override}


def build_postgres_persistence_config_from_url(
    db_url: str,
    *,
    ssl_mode_override: str | None = None,
) -> PersistenceConfig:
    """Build a ``PersistenceConfig`` from a libpq-style URL.

    Accepts the canonical form the CLI compose template emits:
    ``postgresql://user:password@host:5432/dbname``. Userinfo,
    hostname, port, and path are URL-decoded so credentials with
    reserved characters survive the round-trip.

    The default ``ssl_mode`` from ``PostgresConfig`` (``"require"``)
    rejects plaintext connections; for local Docker compose where the
    backend talks to Postgres over an internal network without TLS,
    callers can pass ``ssl_mode_override`` (typically sourced from
    ``SYNTHORG_POSTGRES_SSL_MODE``).

    Returns:
        Result of type ``PersistenceConfig``.
    """
    host, port, database, username, password = _validate_postgres_url(db_url)
    ssl_kwargs = _normalize_ssl_mode_kwargs(ssl_mode_override)
    pg_config = PostgresConfig(
        host=host,
        port=port,
        database=database,
        username=username,
        password=SecretStr(password),
        **ssl_kwargs,
    )
    return PersistenceConfig(backend="postgres", postgres=pg_config)


def build_filesystem_artifact_storage(*, data_dir: Path) -> ArtifactStorageBackend:
    """Build a filesystem-backed artifact storage rooted at *data_dir*.

    Returns the ``ArtifactStorageBackend`` protocol surface so callers
    never have to import the concrete ``FileSystemArtifactStorage``
    class.

    Returns:
        Result of type ``ArtifactStorageBackend``.
    """
    return FileSystemArtifactStorage(data_dir=data_dir)


def normalize_ssl_mode_value(raw: str | None) -> str | None:
    """Normalise a raw ``SYNTHORG_POSTGRES_SSL_MODE`` value.

    Trims whitespace and collapses empty / whitespace-only inputs to
    ``None`` so callers receive a clean ``ssl_mode_override`` argument.
    The actual environment read happens in ``api/app.py`` startup
    wiring; this helper stays env-agnostic so the persistence package
    can be reasoned about without its config decisions hidden behind a
    process-wide environment side-effect.

    Returns:
        The matching value, or ``None`` when absent.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


__all__ = [
    "build_filesystem_artifact_storage",
    "build_postgres_persistence_config_from_url",
    "build_sqlite_persistence_config",
    "normalize_ssl_mode_value",
]
