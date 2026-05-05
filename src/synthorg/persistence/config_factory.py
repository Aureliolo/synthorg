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

import os
from typing import TYPE_CHECKING, Any, NoReturn, get_args
from urllib.parse import unquote, urlparse

from pydantic import SecretStr

from synthorg.observability import get_logger
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

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)


def build_sqlite_persistence_config(*, path: str) -> PersistenceConfig:
    """Build a ``PersistenceConfig`` for a SQLite database at *path*.

    Callers (typically ``api/app.py`` auto-wire) get back the abstract
    ``PersistenceConfig`` envelope so they never have to import the
    concrete ``SQLiteConfig`` class directly.
    """
    return PersistenceConfig(
        backend="sqlite",
        sqlite=SQLiteConfig(path=path),
    )


def build_postgres_persistence_config_from_url(  # noqa: C901
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
    """

    def _fail(msg: str, reason: str, cause: Exception | None = None) -> NoReturn:
        logger.warning(API_APP_STARTUP, error=msg, reason=reason)
        raise ValueError(msg) from cause

    try:
        parsed = urlparse(db_url)
    except ValueError as exc:
        _fail(
            f"SYNTHORG_DATABASE_URL could not be parsed: {exc}",
            "url_parse_failed",
            exc,
        )
    if parsed.query:
        _fail(
            "SYNTHORG_DATABASE_URL must not include query parameters; use "
            "SYNTHORG_POSTGRES_SSL_MODE for ssl_mode overrides",
            "unsupported_query_params",
        )
    if parsed.scheme not in {"postgres", "postgresql"}:
        _fail(
            f"SYNTHORG_DATABASE_URL scheme {parsed.scheme!r} is not "
            f"supported; expected 'postgresql://...'",
            "invalid_scheme",
        )
    try:
        hostname = parsed.hostname
        parsed_port = parsed.port
    except ValueError as exc:
        _fail(
            f"SYNTHORG_DATABASE_URL has an invalid host/port: {exc}",
            "invalid_host_port",
            exc,
        )
    if not hostname:
        _fail("SYNTHORG_DATABASE_URL is missing a host component", "missing_host")
    if not parsed.username or not parsed.password:
        _fail(
            "SYNTHORG_DATABASE_URL must include a username and password "
            "(postgresql://user:pass@host:port/db)",
            "missing_credentials",
        )
    database = parsed.path.lstrip("/")
    if not database:
        _fail(
            "SYNTHORG_DATABASE_URL must include a database name in the "
            "path (postgresql://user:pass@host:port/db)",
            "missing_database",
        )

    ssl_kwargs: dict[str, Any] = {}
    if ssl_mode_override:
        valid_modes = set(get_args(PostgresSslMode))
        if ssl_mode_override not in valid_modes:
            _fail(
                f"SYNTHORG_POSTGRES_SSL_MODE={ssl_mode_override!r} is invalid; "
                f"must be one of: {sorted(valid_modes)}",
                "invalid_ssl_mode",
            )
        ssl_kwargs["ssl_mode"] = ssl_mode_override

    pg_config = PostgresConfig(
        host=unquote(hostname),
        port=parsed_port or 5432,
        database=unquote(database),
        username=unquote(parsed.username),
        password=SecretStr(unquote(parsed.password)),
        **ssl_kwargs,
    )
    return PersistenceConfig(backend="postgres", postgres=pg_config)


def build_filesystem_artifact_storage(*, data_dir: Path) -> ArtifactStorageBackend:
    """Build a filesystem-backed artifact storage rooted at *data_dir*.

    Returns the ``ArtifactStorageBackend`` protocol surface so callers
    never have to import the concrete ``FileSystemArtifactStorage``
    class.
    """
    return FileSystemArtifactStorage(data_dir=data_dir)


def resolve_postgres_ssl_mode_from_env() -> str | None:
    """Return the ``SYNTHORG_POSTGRES_SSL_MODE`` override, or ``None``.

    Whitespace-only values become ``None`` so callers receive a clean
    ``ssl_mode_override`` argument.
    """
    raw = (os.environ.get("SYNTHORG_POSTGRES_SSL_MODE") or "").strip()
    return raw or None


__all__ = [
    "build_filesystem_artifact_storage",
    "build_postgres_persistence_config_from_url",
    "build_sqlite_persistence_config",
    "resolve_postgres_ssl_mode_from_env",
]
