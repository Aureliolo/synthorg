"""URL construction + discovery helpers for the yoyo migration runner.

Split from :mod:`synthorg.persistence.migrations` so the runner module
(the five public coroutines) stays under its tier cap. This module holds
the pure / synchronous pieces: the result dataclasses, the
``sqlite:///`` / ``postgresql+psycopg://`` URL builders, revisions-dir
resolution, and the yoyo migration-list discovery wrapper.
"""

import importlib.resources
import math
import shutil
import sqlite3
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal

import psycopg
from yoyo import read_migrations  # type: ignore[import-untyped]
from yoyo.exceptions import (  # type: ignore[import-untyped]
    BadMigration,
    LockTimeout,
    MigrationConflict,
)
from yoyo.migrations import MigrationList  # type: ignore[import-untyped]

from synthorg.core.persistence_errors import MigrationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import PERSISTENCE_MIGRATION_FAILED
from synthorg.persistence.config import PostgresConfig

logger = get_logger(__name__)

BackendName = Literal["sqlite", "postgres"]

_DEFAULT_LOCK_TIMEOUT_SECONDS: Final[int] = 30
"""Seconds to wait for the yoyo DB-level lock before erroring."""

_LIBPQ_MIN_CONNECT_TIMEOUT_SECONDS: Final[int] = 2
"""libpq's ``connect_timeout`` honours integer seconds with a minimum of 2."""

_MIGRATION_FAILURE_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (
    BadMigration,
    LockTimeout,
    MigrationConflict,
    sqlite3.Error,
    psycopg.Error,
    OSError,
)
"""Exception classes wrapped into ``MigrationError`` by the public coroutines.

Yoyo raises its own :mod:`yoyo.exceptions` subset for orchestration
errors, but raw driver errors (``sqlite3.Error`` from the SQLite C
bindings, ``psycopg.Error`` from psycopg 3) propagate through yoyo
when the underlying connection drops mid-apply or the database
refuses an operation (deadlock, permission denied, malformed schema).
Including them here keeps the public API contract honest: every
caller sees ``MigrationError`` regardless of the underlying driver.
"""


@dataclass(frozen=True)
class MigrateResult:
    """Outcome of a migrate / baseline / rollback operation.

    Attributes:
        applied_count: Number of migrations affected (applied, marked,
            or rolled back depending on the operation).
        applied_versions: Ordered tuple of migration ids touched by
            this operation (oldest first for apply / baseline; newest
            first for rollback).
        current_version: The id of the most recent migration the
            database is now at, or ``""`` if no migrations are
            recorded.
    """

    applied_count: int
    applied_versions: tuple[str, ...]
    current_version: str

    def __post_init__(self) -> None:
        """Enforce ``applied_count == len(applied_versions)`` at construction.

        Raises:
            ValueError: If an argument fails validation.
        """
        if len(self.applied_versions) != self.applied_count:
            msg = (
                f"MigrateResult invariant violated: applied_count="
                f"{self.applied_count} but len(applied_versions)="
                f"{len(self.applied_versions)}"
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class MigrateStatus:
    """Current migration state of a database without applying anything.

    Attributes:
        current_version: Most recently applied migration id.
        pending_count: Number of revisions on disk not yet applied.
        pending_versions: Ordered ids of pending migrations.
        applied_versions: Ordered ids of applied migrations (oldest
            first).
    """

    current_version: str
    pending_count: int
    pending_versions: tuple[str, ...]
    applied_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        """Enforce ``pending_count == len(pending_versions)`` at construction.

        Raises:
            ValueError: If an argument fails validation.
        """
        if len(self.pending_versions) != self.pending_count:
            msg = (
                f"MigrateStatus invariant violated: pending_count="
                f"{self.pending_count} but len(pending_versions)="
                f"{len(self.pending_versions)}"
            )
            raise ValueError(msg)


_REVISIONS_PACKAGE: dict[BackendName, str] = {
    "sqlite": "synthorg.persistence.sqlite.revisions",
    "postgres": "synthorg.persistence.postgres.revisions",
}

_NON_MIGRATION_IDS: Final[frozenset[str]] = frozenset({"__init__"})
"""Migration ids that come from the package layout, not real migrations.

Yoyo's ``read_migrations`` accepts any ``*.sql`` / ``*.py`` file in the
revisions directory.  ``__init__.py`` exists so the directory is
importable as a Python sub-package (used by ``importlib.resources``);
filtering it here keeps yoyo from logging it as an empty migration.
"""


def _to_posix(path: str) -> str:
    r"""Convert a filesystem path to forward-slash POSIX form.

    On Windows, ``C:\Users\foo`` becomes ``C:/Users/foo``.  On POSIX
    systems this is a no-op.

    Returns:
        Result of type ``str``.
    """
    return str(PurePosixPath(PureWindowsPath(path)))


def _redact_url(url: str) -> str:
    """Return scheme prefix only, dropping host / credentials / path.

    Returns:
        Result of type ``str``.
    """
    scheme_end = url.find("://")
    if scheme_end == -1:
        return "REDACTED"
    return f"{url[:scheme_end]}://..."


def _safe_close(b: object, *, context: str) -> None:
    """Close the yoyo backend connection without masking the caller's error.

    Yoyo's ``backend.connection.close()`` can raise driver-specific
    errors (network drop, file-system error, already-closed handle).
    When the surrounding ``finally`` runs during exception propagation,
    a fresh exception from close would overwrite the original migration
    failure; this helper logs and swallows the cleanup error so the
    caller's exception stays first.

    Also tolerates a missing ``connection`` attribute: yoyo normally
    sets it in ``Backend.__init__``, but a partial construction path
    that raises after we hold ``b`` would leave the attribute unset
    and the resulting ``AttributeError`` would mask the original
    migration failure.
    """
    connection = getattr(b, "connection", None)
    if connection is None:
        return
    try:
        connection.close()
    except (sqlite3.Error, psycopg.Error, OSError) as cleanup_exc:
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            context=context,
            error_type=type(cleanup_exc).__name__,
            error=safe_error_description(cleanup_exc),
        )


def to_sqlite_url(path: str) -> str:
    r"""Build a yoyo-compatible SQLite URL.

    Yoyo expects ``sqlite:///`` followed by the absolute or relative
    path.  On Windows, drive-letter paths use forward slashes
    (``sqlite:///C:/path/db.sqlite``).

    Args:
        path: Database file path (native OS form accepted).

    Returns:
        Yoyo-compatible ``sqlite:///`` URL.

    Raises:
        MigrationError: If *path* is ``":memory:"`` -- yoyo cannot
            share an in-memory database with the caller's aiosqlite
            connection.
    """
    if path == ":memory:":
        msg = (
            "yoyo cannot migrate in-memory databases; "
            "use a file-backed database path instead."
        )
        logger.error(PERSISTENCE_MIGRATION_FAILED, error=msg)
        raise MigrationError(msg)
    return f"sqlite:///{_to_posix(path)}"


def to_postgres_url(config: PostgresConfig) -> str:
    """Build a yoyo-compatible Postgres URL using the psycopg 3 backend.

    The ``postgresql+psycopg://`` scheme routes to yoyo's
    ``PostgresqlPsycopgBackend``, which connects via psycopg 3 (the
    driver the rest of the codebase already uses).  The default
    ``postgresql://`` scheme would route to psycopg2, which is not a
    project dependency.

    Yoyo runs in-process, so the password embedded in the URL is
    never exposed through the OS process listing.  We still redact
    the URL in logs via :func:`_redact_url` so accidental log lines
    do not leak it.

    libpq's ``connect_timeout`` accepts integer seconds with a minimum
    of 2; sub-second configured values are rounded up so a configured
    ``0.5`` second timeout does not silently become "wait
    indefinitely" via ``int(0.5) == 0``.

    Args:
        config: Postgres configuration model.

    Returns:
        ``postgresql+psycopg://user:password@host:port/database`` URL
        with ``sslmode``, ``application_name``, and ``connect_timeout``
        query parameters.
    """
    user = urllib.parse.quote(config.username, safe="")
    password = urllib.parse.quote(config.password.get_secret_value(), safe="")
    database = urllib.parse.quote(config.database, safe="")
    connect_timeout = max(
        _LIBPQ_MIN_CONNECT_TIMEOUT_SECONDS,
        math.ceil(config.connect_timeout_seconds),
    )
    query = urllib.parse.urlencode(
        {
            "sslmode": config.ssl_mode,
            "application_name": config.application_name,
            "connect_timeout": connect_timeout,
        }
    )
    return (
        f"postgresql+psycopg://{user}:{password}"
        f"@{config.host}:{config.port}/{database}?{query}"
    )


def revisions_dir(backend: BackendName) -> Path:
    """Resolve the installed revisions directory for a backend.

    Uses :mod:`importlib.resources` to locate the ``revisions``
    package inside the installed ``synthorg`` distribution so editable
    and wheel installs both work.

    Args:
        backend: Which backend's revisions to resolve.

    Returns:
        Filesystem :class:`Path` to the revisions directory.

    Raises:
        MigrationError: If the revisions package cannot be located.
    """
    pkg = _REVISIONS_PACKAGE[backend]
    try:
        ref = importlib.resources.files(pkg)
        return Path(str(ref))
    except (ModuleNotFoundError, TypeError) as exc:
        msg = f"Cannot locate migration revisions package: {pkg}"
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MigrationError(msg) from exc


def copy_revisions(dest: Path, *, backend: BackendName = "sqlite") -> Path:
    """Copy the revisions directory to *dest* and return the destination.

    Test fixtures use this to give each xdist worker a private copy of
    the revisions directory.  Yoyo's lock is DB-level (not filesystem)
    so contention is impossible when each worker also has its own DB
    file, but copying keeps tests symmetric with the installed layout.

    Args:
        dest: Destination directory (e.g. ``tmp_path / "revisions"``).
        backend: Which backend's revisions to copy.

    Returns:
        The *dest* :class:`Path`.

    Raises:
        MigrationError: If the copy fails.
    """
    src = revisions_dir(backend)
    try:
        shutil.copytree(str(src), str(dest))
    except (OSError, shutil.Error) as exc:
        msg = (
            f"Failed to copy migration revisions to {dest}: "
            f"{safe_error_description(exc)}"
        )
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MigrationError(msg) from exc
    return dest


def _resolve_revisions_path(
    revisions_path: Path | None,
    backend: BackendName,
) -> Path:
    """Return *revisions_path* if provided, otherwise the installed dir.

    Returns:
        Result of type ``Path``.
    """
    return revisions_path if revisions_path is not None else revisions_dir(backend)


def _discover(rev_path: Path) -> MigrationList:
    """Return the yoyo migration list for *rev_path*, minus package files.

    Wraps ``yoyo.read_migrations`` and drops migrations whose id is in
    :data:`_NON_MIGRATION_IDS`.  Preserves the original
    ``post_apply`` list (also filtered).

    Returns:
        Result of type ``MigrationList``.
    """
    discovered = read_migrations(str(rev_path))
    filtered_items = [m for m in discovered if m.id not in _NON_MIGRATION_IDS]
    filtered_post = [p for p in discovered.post_apply if p.id not in _NON_MIGRATION_IDS]
    return MigrationList(filtered_items, filtered_post)


__all__ = [
    "BackendName",
    "MigrateResult",
    "MigrateStatus",
    "copy_revisions",
    "revisions_dir",
    "to_postgres_url",
    "to_sqlite_url",
]
