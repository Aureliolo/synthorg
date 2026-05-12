"""Schema migrations via yoyo-migrations.

Architecture
------------

Migrations live as ``*.sql`` (and optionally ``*.py``) files under
``src/synthorg/persistence/{sqlite,postgres}/revisions/``.  Yoyo applies
them in lexicographic filename order.  We keep the
``<14-digit-timestamp>_<name>.sql`` convention so the existing
"single migration per PR" git-diff gate continues to work unchanged.

Yoyo features we lean on:

* **Content-hash tracking** in ``_yoyo_migration``: yoyo refuses to
  re-apply a previously-applied migration whose on-disk content has
  changed.  This obsoletes any standalone checksum sidecar.
* **Per-migration transactions** (default).  A migration that needs to
  run outside a transaction (e.g. Postgres ``CREATE INDEX
  CONCURRENTLY``) sets ``__transactional__ = False`` in the file.
* **Audit log** in ``_yoyo_log``: timestamped history of every apply /
  rollback / mark, populated automatically.
* **DB-level lock** via ``backend.lock(timeout=...)``: serialises
  concurrent migrators across processes.  ``break_lock`` recovers from
  a crashed run.
* **Mark-as-applied** via ``backend.mark_migrations``: stamps a fresh
  database as already-at-version without executing SQL (the
  fresh-install Postgres path).
* **psycopg 3 backend**: the ``postgresql+psycopg://`` URL scheme
  routes to ``PostgresqlPsycopgBackend`` and reuses the project's
  existing psycopg 3 dependency; no psycopg2 needed.

Yoyo is synchronous; every public coroutine wraps the blocking calls
in ``asyncio.to_thread`` so callers in the persistence backends'
``migrate()`` methods stay loop-friendly.  Yoyo's blocking work cannot
be cancelled cleanly mid-flight; if the surrounding task is cancelled
the worker thread runs to completion and any DB lock is released by
yoyo's ``with backend.lock():`` finally clause.
"""

import asyncio
import importlib.resources
import math
import shutil
import sqlite3
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Final, Literal

import psycopg
from yoyo import get_backend, read_migrations  # type: ignore[import-untyped]
from yoyo.exceptions import (  # type: ignore[import-untyped]
    BadMigration,
    LockTimeout,
    MigrationConflict,
)
from yoyo.migrations import MigrationList  # type: ignore[import-untyped]

from synthorg.core.persistence_errors import MigrationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_MIGRATION_COMPLETED,
    PERSISTENCE_MIGRATION_FAILED,
    PERSISTENCE_MIGRATION_STARTED,
)
from synthorg.persistence.config import PostgresConfig  # noqa: TC001

logger = get_logger(__name__)

BackendName = Literal["sqlite", "postgres"]

_DEFAULT_LOCK_TIMEOUT_SECONDS: Final[int] = 30
"""Seconds to wait for the yoyo DB-level lock before erroring."""

_LIBPQ_MIN_CONNECT_TIMEOUT_SECONDS: Final[int] = 2
"""libpq's ``connect_timeout`` honours integer seconds with a minimum of 2."""


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
        """Enforce ``applied_count == len(applied_versions)`` at construction."""
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
        """Enforce ``pending_count == len(pending_versions)`` at construction."""
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


def _to_posix(path: str) -> str:
    r"""Convert a filesystem path to forward-slash POSIX form.

    On Windows, ``C:\Users\foo`` becomes ``C:/Users/foo``.  On POSIX
    systems this is a no-op.
    """
    return str(PurePosixPath(PureWindowsPath(path)))


def _redact_url(url: str) -> str:
    """Return scheme prefix only, dropping host / credentials / path."""
    scheme_end = url.find("://")
    if scheme_end == -1:
        return "REDACTED"
    return f"{url[:scheme_end]}://..."


def _safe_close(b: Any, *, context: str) -> None:
    """Close the yoyo backend connection without masking the caller's error.

    Yoyo's ``backend.connection.close()`` can raise driver-specific
    errors (network drop, file-system error, already-closed handle).
    When the surrounding ``finally`` runs during exception propagation,
    a fresh exception from close would overwrite the original migration
    failure; this helper logs and swallows the cleanup error so the
    caller's exception stays first.
    """
    try:
        b.connection.close()
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
    """Return *revisions_path* if provided, otherwise the installed dir."""
    return revisions_path if revisions_path is not None else revisions_dir(backend)


_NON_MIGRATION_IDS: Final[frozenset[str]] = frozenset({"__init__"})
"""Migration ids that come from the package layout, not real migrations.

Yoyo's ``read_migrations`` accepts any ``*.sql`` / ``*.py`` file in the
revisions directory.  ``__init__.py`` exists so the directory is
importable as a Python sub-package (used by ``importlib.resources``);
filtering it here keeps yoyo from logging it as an empty migration.
"""


def _discover(rev_path: Path) -> MigrationList:
    """Return the yoyo migration list for *rev_path*, minus package files.

    Wraps ``yoyo.read_migrations`` and drops migrations whose id is in
    :data:`_NON_MIGRATION_IDS`.  Preserves the original
    ``post_apply`` list (also filtered).
    """
    discovered = read_migrations(str(rev_path))
    filtered_items = [m for m in discovered if m.id not in _NON_MIGRATION_IDS]
    filtered_post = [p for p in discovered.post_apply if p.id not in _NON_MIGRATION_IDS]
    return MigrationList(filtered_items, filtered_post)


async def migrate_apply(
    db_url: str,
    *,
    revisions_path: Path | None = None,
    backend: BackendName = "sqlite",
    lock_timeout_seconds: int = _DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> MigrateResult:
    """Apply pending migrations against *db_url*.

    Each migration runs in its own transaction by default.  A
    migration whose content changed since it was previously applied is
    detected via yoyo's content-hash check and refused; rerun the
    migration as a new revision with a fresh timestamp instead.

    Args:
        db_url: Yoyo-format database URL.
        revisions_path: Override for the on-disk revisions directory.
            When ``None``, the installed package location is used.
        backend: Which backend's revisions to use when
            *revisions_path* is not provided.
        lock_timeout_seconds: Seconds to wait for the DB lock before
            failing.

    Returns:
        :class:`MigrateResult` with the applied count + ids and the
        new current version.

    Raises:
        MigrationError: If yoyo refuses the apply or fails mid-run.
    """
    rev_path = _resolve_revisions_path(revisions_path, backend)
    logger.info(
        PERSISTENCE_MIGRATION_STARTED,
        db_url=_redact_url(db_url),
        backend=backend,
        operation="apply",
    )

    def _apply() -> tuple[int, tuple[str, ...], str]:
        b = get_backend(db_url)
        try:
            migrations = _discover(rev_path)
            with b.lock(timeout=lock_timeout_seconds):
                pending = b.to_apply(migrations)
                pending_ids = tuple(m.id for m in pending)
                b.apply_migrations(pending)
                applied_after = b.to_rollback(migrations)
                applied_ids = tuple(reversed([m.id for m in applied_after]))
        finally:
            _safe_close(b, context="close_after_apply")
        current = applied_ids[-1] if applied_ids else ""
        return len(pending_ids), pending_ids, current

    try:
        applied_count, applied_versions, current_version = await asyncio.to_thread(
            _apply,
        )
    except (BadMigration, LockTimeout, MigrationConflict, OSError) as exc:
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            db_url=_redact_url(db_url),
            backend=backend,
            operation="apply",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"yoyo apply failed: {safe_error_description(exc)}"
        raise MigrationError(msg) from exc

    logger.info(
        PERSISTENCE_MIGRATION_COMPLETED,
        operation="apply",
        applied_count=applied_count,
        current_version=current_version,
    )
    return MigrateResult(
        applied_count=applied_count,
        applied_versions=applied_versions,
        current_version=current_version,
    )


async def migrate_status(
    db_url: str,
    *,
    revisions_path: Path | None = None,
    backend: BackendName = "sqlite",
) -> MigrateStatus:
    """Return current migration state without applying anything.

    Args:
        db_url: Yoyo-format database URL.
        revisions_path: Override for the on-disk revisions directory.
        backend: Which backend's revisions to use when
            *revisions_path* is not provided.

    Returns:
        :class:`MigrateStatus` snapshot.

    Raises:
        MigrationError: If yoyo cannot connect or read the migration
            history.
    """
    rev_path = _resolve_revisions_path(revisions_path, backend)

    def _status() -> MigrateStatus:
        b = get_backend(db_url)
        try:
            migrations = _discover(rev_path)
            pending = tuple(m.id for m in b.to_apply(migrations))
            applied_reversed = [m.id for m in b.to_rollback(migrations)]
            applied = tuple(reversed(applied_reversed))
        finally:
            _safe_close(b, context="close_after_status")
        current = applied[-1] if applied else ""
        return MigrateStatus(
            current_version=current,
            pending_count=len(pending),
            pending_versions=pending,
            applied_versions=applied,
        )

    try:
        return await asyncio.to_thread(_status)
    except (BadMigration, LockTimeout, MigrationConflict, OSError) as exc:
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            db_url=_redact_url(db_url),
            backend=backend,
            operation="status",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"yoyo status failed: {safe_error_description(exc)}"
        raise MigrationError(msg) from exc


async def migrate_baseline(
    db_url: str,
    *,
    revisions_path: Path | None = None,
    backend: BackendName = "sqlite",
    lock_timeout_seconds: int = _DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> MigrateResult:
    """Mark every revision as applied without executing its SQL.

    Used for fresh installs whose schema was created by some other
    means (e.g. restored from a backup, provisioned by an external
    tool, or populated by a one-shot ``CREATE TABLE`` script during
    bootstrap).  After baseline, ``migrate_apply`` is a no-op until
    new revisions are added.

    Args:
        db_url: Yoyo-format database URL.
        revisions_path: Override for the on-disk revisions directory.
        backend: Which backend's revisions to use when
            *revisions_path* is not provided.
        lock_timeout_seconds: Seconds to wait for the DB lock.

    Returns:
        :class:`MigrateResult` reporting how many revisions were
        marked.

    Raises:
        MigrationError: If yoyo cannot mark the revisions.
    """
    rev_path = _resolve_revisions_path(revisions_path, backend)
    logger.info(
        PERSISTENCE_MIGRATION_STARTED,
        db_url=_redact_url(db_url),
        backend=backend,
        operation="baseline",
    )

    def _mark() -> tuple[int, tuple[str, ...], str]:
        b = get_backend(db_url)
        try:
            migrations = _discover(rev_path)
            with b.lock(timeout=lock_timeout_seconds):
                unapplied = b.to_apply(migrations)
                marked_ids = tuple(m.id for m in unapplied)
                b.mark_migrations(unapplied)
        finally:
            _safe_close(b, context="close_after_baseline")
        current = marked_ids[-1] if marked_ids else ""
        return len(marked_ids), marked_ids, current

    try:
        marked_count, marked_versions, current_version = await asyncio.to_thread(
            _mark,
        )
    except (BadMigration, LockTimeout, MigrationConflict, OSError) as exc:
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            db_url=_redact_url(db_url),
            backend=backend,
            operation="baseline",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"yoyo baseline failed: {safe_error_description(exc)}"
        raise MigrationError(msg) from exc

    logger.info(
        PERSISTENCE_MIGRATION_COMPLETED,
        operation="baseline",
        marked_count=marked_count,
        current_version=current_version,
    )
    return MigrateResult(
        applied_count=marked_count,
        applied_versions=marked_versions,
        current_version=current_version,
    )


async def migrate_rollback(
    db_url: str,
    *,
    target_version: str,
    revisions_path: Path | None = None,
    backend: BackendName = "sqlite",
    lock_timeout_seconds: int = _DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> MigrateResult:
    """Roll back applied migrations newer than *target_version*.

    Pure ``.sql`` revisions have no rollback step and cannot be rolled
    back; if any revision newer than *target_version* lacks rollback
    SQL, yoyo raises and this function surfaces the failure as a
    :class:`MigrationError`.  Author new revisions as ``.py`` files
    using ``step("forward", "rollback")`` to enable rollback.

    Args:
        db_url: Yoyo-format database URL.
        target_version: The migration id to roll back to (this id
            stays applied; everything strictly newer is reverted).
            Pass ``""`` to roll back every applied migration.
        revisions_path: Override for the on-disk revisions directory.
        backend: Which backend's revisions to use when
            *revisions_path* is not provided.
        lock_timeout_seconds: Seconds to wait for the DB lock.

    Returns:
        :class:`MigrateResult` with the rolled-back ids in
        rollback order (newest first).

    Raises:
        MigrationError: If a rollback step is missing or yoyo fails
            mid-run.
    """
    rev_path = _resolve_revisions_path(revisions_path, backend)
    logger.info(
        PERSISTENCE_MIGRATION_STARTED,
        db_url=_redact_url(db_url),
        backend=backend,
        operation="rollback",
        target_version=target_version,
    )

    def _rollback() -> tuple[int, tuple[str, ...]]:
        b = get_backend(db_url)
        try:
            migrations = _discover(rev_path)
            with b.lock(timeout=lock_timeout_seconds):
                applied_in_rollback_order = list(b.to_rollback(migrations))
                to_revert = []
                for m in applied_in_rollback_order:
                    if m.id == target_version:
                        break
                    to_revert.append(m)
                rolled_ids = tuple(m.id for m in to_revert)
                if to_revert:
                    bundle = migrations.__class__(to_revert, [])
                    b.rollback_migrations(bundle)
        finally:
            _safe_close(b, context="close_after_rollback")
        return len(rolled_ids), rolled_ids

    try:
        rolled_count, rolled_versions = await asyncio.to_thread(_rollback)
    except (BadMigration, LockTimeout, MigrationConflict, OSError) as exc:
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            db_url=_redact_url(db_url),
            backend=backend,
            operation="rollback",
            target_version=target_version,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"yoyo rollback failed: {safe_error_description(exc)}"
        raise MigrationError(msg) from exc

    logger.info(
        PERSISTENCE_MIGRATION_COMPLETED,
        operation="rollback",
        rolled_count=rolled_count,
        target_version=target_version,
    )
    return MigrateResult(
        applied_count=rolled_count,
        applied_versions=rolled_versions,
        current_version=target_version,
    )


async def break_lock(db_url: str) -> None:
    """Forcibly release a stale yoyo lock.

    Yoyo holds a DB-level lock during apply / rollback / baseline.  If
    a process crashes mid-run the lock can stay held; this clears it.
    Use sparingly and only when no other process is actively
    migrating.

    Args:
        db_url: Yoyo-format database URL.

    Raises:
        MigrationError: If yoyo cannot connect or the lock release
            fails.
    """

    def _break() -> None:
        b = get_backend(db_url)
        try:
            b.break_lock()
        finally:
            _safe_close(b, context="close_after_break_lock")

    try:
        await asyncio.to_thread(_break)
    except (BadMigration, LockTimeout, MigrationConflict, OSError) as exc:
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            db_url=_redact_url(db_url),
            operation="break_lock",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"yoyo break_lock failed: {safe_error_description(exc)}"
        raise MigrationError(msg) from exc
