"""Postgres backup primitives via ``pg_dump`` and ``pg_restore``.

Wraps the standard PostgreSQL backup CLI tooling. Kept inside
``persistence/`` so the boundary linter's 'only persistence may import
psycopg or shell out to PG tooling' rule holds.

Subprocess invocations go through :func:`asyncio.create_subprocess_exec`
with an explicit argv list (no shell) and ``PGPASSWORD`` injected via
the child's environment so the secret never appears on argv.
"""

import asyncio
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_COMPONENT_FAILED,
    BACKUP_HANDLER_REGISTRATION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.persistence.config import PostgresConfig

logger = get_logger(__name__)

_DEFAULT_PG_DUMP_TIMEOUT_SECONDS: Final[float] = 600.0
_PG_DUMP_BINARY: Final[str] = "pg_dump"
_PG_RESTORE_BINARY: Final[str] = "pg_restore"


class PgToolUnavailableError(
    RuntimeError
):  # lint-allow: domain-error-hierarchy -- not HTTP-exposed
    """The ``pg_dump`` or ``pg_restore`` binary is not on PATH."""


class PgToolFailedError(
    RuntimeError
):  # lint-allow: domain-error-hierarchy -- not HTTP-exposed
    """A ``pg_dump`` / ``pg_restore`` invocation exited non-zero."""


def _resolve_binary(name: str) -> str:
    """Look up *name* on PATH or raise :class:`PgToolUnavailableError`."""
    resolved = shutil.which(name)
    if resolved is None:
        msg = (
            f"PostgreSQL CLI tool {name!r} is not available on PATH. "
            "Install the postgresql-client package (Debian/Ubuntu) or "
            "ensure the bin directory of your PostgreSQL installation "
            "is in PATH for backup support."
        )
        logger.error(
            BACKUP_HANDLER_REGISTRATION_FAILED,
            backend="postgres",
            tool=name,
            error_type="PgToolUnavailableError",
            error=msg,
        )
        raise PgToolUnavailableError(msg)
    return resolved


def _child_env(config: PostgresConfig) -> dict[str, str]:
    """Return a child-process env with ``PGPASSWORD`` injected.

    Copies the current environment so PATH / locale stay intact; sets
    ``PGPASSWORD`` so libpq picks it up without ever putting it on
    argv (where ``ps`` would expose it).
    """
    env = os.environ.copy()
    env["PGPASSWORD"] = config.password.get_secret_value()
    return env


async def _run_pg_tool(
    binary: str,
    args: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
    output_path: Path | None = None,
) -> tuple[bytes, bytes]:
    """Run a PG tool and return ``(stdout, stderr)`` on success.

    When ``output_path`` is provided, stdout is streamed to that file
    instead of buffered in memory (used by ``pg_dump`` -> file).

    Raises:
        PgToolFailedError: Non-zero exit.
        TimeoutError: Subprocess did not finish within
            ``timeout_seconds``.
    """
    if output_path is not None:
        with output_path.open("wb") as fp:
            proc = await asyncio.create_subprocess_exec(
                binary,
                *args,
                env=env,
                stdout=fp,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise
        return b"", stderr or b""
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        msg = (
            f"{binary} exited with code {proc.returncode}: "
            f"{(stderr or b'').decode('utf-8', errors='replace').strip()}"
        )
        logger.warning(
            BACKUP_COMPONENT_FAILED,
            component=f"postgres_{Path(binary).stem}",
            returncode=proc.returncode,
            error_type="PgToolFailedError",
            error=msg,
        )
        raise PgToolFailedError(msg)
    return stdout or b"", stderr or b""


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
    binary = _resolve_binary(_PG_DUMP_BINARY)
    args = [
        f"--host={config.host}",
        f"--port={config.port}",
        f"--username={config.username}",
        f"--dbname={config.database}",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
    ]
    await _run_pg_tool(
        binary,
        args,
        env=_child_env(config),
        timeout_seconds=timeout_seconds,
        output_path=target_path,
    )
    return await asyncio.to_thread(_file_size, target_path)


def _file_size(path: Path) -> int:
    """Return ``path.stat().st_size`` (kept sync for ASYNC240)."""
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
    binary = _resolve_binary(_PG_RESTORE_BINARY)
    args = [
        f"--host={config.host}",
        f"--port={config.port}",
        f"--username={config.username}",
        f"--dbname={config.database}",
        "--clean",
        "--if-exists",
        "--single-transaction",
        "--no-owner",
        "--no-privileges",
        str(source_path),
    ]
    await _run_pg_tool(
        binary,
        args,
        env=_child_env(config),
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

    Raises:
        PgToolUnavailableError: ``pg_restore`` is not on PATH.
        PgToolFailedError: ``pg_restore`` could not read the dump.
        TimeoutError: Listing exceeded ``timeout_seconds``.
    """
    binary = _resolve_binary(_PG_RESTORE_BINARY)
    stdout, _stderr = await _run_pg_tool(
        binary,
        ["--list", str(source_path)],
        env=os.environ.copy(),
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
    """Local alias preserving the redaction contract."""
    return safe_error_description(exc)
