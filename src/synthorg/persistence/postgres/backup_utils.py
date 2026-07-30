# module-kind: code
"""Postgres backup primitives via ``pg_dump`` and ``pg_restore``.

What to ask the PostgreSQL CLI tooling for; :mod:`.pg_subprocess` owns how a
tool is run at all (binary resolution, the child environment, the two execution
shapes). Kept inside ``persistence/`` so the boundary linter's 'only persistence
may import psycopg or shell out to PG tooling' rule holds.
"""

import asyncio
from pathlib import Path
from typing import Final

from synthorg.observability import safe_error_description
from synthorg.persistence.config import PostgresConfig
from synthorg.persistence.postgres.pg_subprocess import (
    PgToolFailedError,
    PgToolUnavailableError,
    child_env,
    minimal_local_env,
    resolve_binary,
    run_pg_tool,
)

_DEFAULT_PG_DUMP_TIMEOUT_SECONDS: Final[float] = 600.0
_PG_DUMP_BINARY: Final[str] = "pg_dump"
_PG_RESTORE_BINARY: Final[str] = "pg_restore"


def ensure_pg_tools_available() -> None:
    """Verify ``pg_dump`` and ``pg_restore`` are on PATH.

    Resolves both binaries at the caller's location so missing tooling
    surfaces during factory dispatch with a
    :data:`BACKUP_HANDLER_REGISTRATION_FAILED` event, instead of the
    first scheduled backup attempt.

    Raises:
        PgToolUnavailableError: Either binary is missing from PATH.
    """
    resolve_binary(_PG_DUMP_BINARY)
    resolve_binary(_PG_RESTORE_BINARY)


async def pg_dump_to_file(
    config: PostgresConfig,
    target_path: Path,
    *,
    timeout_seconds: float = _DEFAULT_PG_DUMP_TIMEOUT_SECONDS,
) -> int:
    """Run ``pg_dump -Fc`` against *config*, writing to *target_path*.

    Uses custom format (``-Fc``) for compression + parallel restore
    support. The output file is the input to :func:`pg_restore_from_file`
    and :func:`pg_restore_list`.

    Args:
        config: PostgresConfig for the source database.
        target_path: File path the dump is written to.
        timeout_seconds: Maximum seconds before the dump is killed.

    Returns:
        Size of the dump file in bytes.

    Raises:
        PgToolUnavailableError: ``pg_dump`` is not on PATH.
        PgToolFailedError: ``pg_dump`` exited non-zero.
        TimeoutError: ``pg_dump`` exceeded ``timeout_seconds``.
    """
    binary = resolve_binary(_PG_DUMP_BINARY)
    # Connection parameters travel via the PG* env vars set in ``child_env``,
    # never argv: a ``--dbname=`` value is a libpq conninfo slot, so a name
    # carrying ``=`` or ``://`` could redirect the dump or weaken ``sslmode``.
    # ``pg_dump`` connects on its own, so it needs no database on argv.
    args = [
        "--format=custom",
        "--no-owner",
        "--no-privileges",
    ]
    await run_pg_tool(
        binary,
        args,
        env=child_env(config),
        timeout_seconds=timeout_seconds,
        output_path=target_path,
    )
    return await asyncio.to_thread(_file_size, target_path)


def _file_size(path: Path) -> int:
    """Return ``path.stat().st_size`` (kept sync for ASYNC240).

    Returns:
        Numeric result of the operation.
    """
    return path.stat().st_size


async def pg_restore_from_file(
    config: PostgresConfig,
    source_path: Path,
    *,
    timeout_seconds: float = _DEFAULT_PG_DUMP_TIMEOUT_SECONDS,
) -> None:
    """Restore *source_path* into *config*'s database via ``pg_restore``.

    Uses ``--clean --if-exists`` so existing objects are dropped first.
    Single transaction (``--single-transaction``) so a partial failure
    leaves the database unchanged.

    Raises:
        PgToolUnavailableError: ``pg_restore`` is not on PATH.
        PgToolFailedError: ``pg_restore`` exited non-zero.
        TimeoutError: ``pg_restore`` exceeded ``timeout_seconds``.
    """
    binary = resolve_binary(_PG_RESTORE_BINARY)
    # ``--dbname`` is what selects restoring into a database at all: given no
    # database on argv, ``pg_restore`` refuses outright (or, on older versions,
    # writes a SQL script to stdout, which this helper discards -- a restore
    # that reports success and changes nothing). ``PGDATABASE`` cannot stand in
    # for it; libpq reads that only once a connection is being made, and this
    # flag is what decides whether one is. Safe on argv because
    # ``PostgresConfig`` refuses a name libpq's ``expand_dbname`` would
    # reinterpret as a conninfo string; every other connection parameter still
    # travels by env (see ``child_env``).
    args = [
        f"--dbname={config.database}",
        "--clean",
        "--if-exists",
        "--single-transaction",
        "--no-owner",
        "--no-privileges",
        str(source_path),
    ]
    await run_pg_tool(
        binary,
        args,
        env=child_env(config),
        timeout_seconds=timeout_seconds,
    )


async def pg_restore_list(
    source_path: Path,
    *,
    timeout_seconds: float = _DEFAULT_PG_DUMP_TIMEOUT_SECONDS,
) -> int:
    """Run ``pg_restore --list`` against *source_path*, returning entry count.

    A non-empty TOC indicates the dump file is structurally readable.
    Returns ``0`` if the listing succeeds but contains no entries (which
    callers treat as an invalid backup).

    Validation is performed against the local dump file only;
    ``pg_restore --list`` does not connect to a database, so no
    ``PostgresConfig`` is required and ``PGPASSWORD`` is intentionally
    not injected. Validating a dump that lives on a remote host would
    require fetching it locally first.

    Raises:
        PgToolUnavailableError: ``pg_restore`` is not on PATH.
        PgToolFailedError: ``pg_restore`` could not read the dump.
        TimeoutError: Listing exceeded ``timeout_seconds``.

    Returns:
        Numeric result of the operation.
    """
    binary = resolve_binary(_PG_RESTORE_BINARY)
    stdout, _stderr = await run_pg_tool(
        binary,
        ["--list", str(source_path)],
        env=minimal_local_env(),
        timeout_seconds=timeout_seconds,
    )
    # ``pg_restore --list`` emits one TOC entry per line; comments start
    # with ';' and are not counted.
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    entries = [
        line for line in lines if line.strip() and not line.lstrip().startswith(";")
    ]
    return len(entries)


def safe_error(exc: BaseException) -> str:
    """Local alias preserving the redaction contract.

    Returns:
        Result of type ``str``.
    """
    return safe_error_description(exc)


__all__ = [
    "PgToolFailedError",
    "PgToolUnavailableError",
    "ensure_pg_tools_available",
    "pg_dump_to_file",
    "pg_restore_from_file",
    "pg_restore_list",
    "safe_error",
]
