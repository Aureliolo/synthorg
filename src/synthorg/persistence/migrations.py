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
from pathlib import Path

from yoyo import get_backend  # type: ignore[import-untyped]

from synthorg.core.persistence_errors import MigrationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_MIGRATION_COMPLETED,
    PERSISTENCE_MIGRATION_FAILED,
    PERSISTENCE_MIGRATION_STARTED,
)
from synthorg.persistence.migration_helpers import (
    _DEFAULT_LOCK_TIMEOUT_SECONDS,
    _MIGRATION_FAILURE_EXCEPTIONS,
    BackendName,
    MigrateResult,
    MigrateStatus,
    _discover,
    _redact_url,
    _resolve_revisions_path,
    _safe_close,
    copy_revisions,
    revisions_dir,
    to_postgres_url,
    to_sqlite_url,
)

logger = get_logger(__name__)

__all__ = [
    "BackendName",
    "MigrateResult",
    "MigrateStatus",
    "break_lock",
    "copy_revisions",
    "migrate_apply",
    "migrate_baseline",
    "migrate_rollback",
    "migrate_status",
    "revisions_dir",
    "to_postgres_url",
    "to_sqlite_url",
]


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
        """Run yoyo's apply step inside the locked backend session.

        Returns:
            ``(applied_count, applied_steps, head_revision)`` after apply.
        """
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
    except _MIGRATION_FAILURE_EXCEPTIONS as exc:
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
        """Compute pending / applied migration sets and the current head.

        Returns:
            Snapshot of the current head plus the unapplied tail.
        """
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
    except _MIGRATION_FAILURE_EXCEPTIONS as exc:
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
        """Mark all discovered migrations as applied without running them.

        Returns:
            ``(marked_count, marked_steps, head_revision)`` after mark.
        """
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
    except _MIGRATION_FAILURE_EXCEPTIONS as exc:
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
        """Roll back applied migrations down to ``target_version``.

        Returns:
            ``(rolled_back_count, rolled_back_steps)`` after rollback.

        Raises:
            MigrationError: If the underlying call raises.
        """
        b = get_backend(db_url)
        try:
            migrations = _discover(rev_path)
            with b.lock(timeout=lock_timeout_seconds):
                applied_in_rollback_order = list(b.to_rollback(migrations))
                to_revert = []
                # An empty target_version means "roll back everything", so it
                # is implicitly valid. A non-empty target_version must appear
                # in the applied set, otherwise we would silently revert every
                # applied migration and report the unknown version as current.
                target_found = target_version == ""
                for m in applied_in_rollback_order:
                    if m.id == target_version:
                        target_found = True
                        break
                    to_revert.append(m)
                if not target_found:
                    msg = (
                        f"Unknown rollback target version: {target_version!r}"
                        f" (not in applied migrations)"
                    )
                    raise MigrationError(msg)
                rolled_ids = tuple(m.id for m in to_revert)
                if to_revert:
                    bundle = migrations.__class__(to_revert, [])
                    b.rollback_migrations(bundle)
        finally:
            _safe_close(b, context="close_after_rollback")
        return len(rolled_ids), rolled_ids

    try:
        rolled_count, rolled_versions = await asyncio.to_thread(_rollback)
    except MigrationError as exc:
        # The unknown-target guard inside ``_rollback`` raises
        # ``MigrationError`` directly; that pre-validated failure must still
        # surface through ``PERSISTENCE_MIGRATION_FAILED`` so observability
        # treats it identically to a yoyo / driver failure. Re-raise as-is;
        # no need to wrap an already-typed ``MigrationError`` into another.
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            db_url=_redact_url(db_url),
            backend=backend,
            operation="rollback",
            target_version=target_version,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    except _MIGRATION_FAILURE_EXCEPTIONS as exc:
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
        """Release the backend's migration lock unconditionally."""
        b = get_backend(db_url)
        try:
            b.break_lock()
        finally:
            _safe_close(b, context="close_after_break_lock")

    try:
        await asyncio.to_thread(_break)
    except _MIGRATION_FAILURE_EXCEPTIONS as exc:
        logger.warning(
            PERSISTENCE_MIGRATION_FAILED,
            db_url=_redact_url(db_url),
            operation="break_lock",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"yoyo break_lock failed: {safe_error_description(exc)}"
        raise MigrationError(msg) from exc
