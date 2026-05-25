"""Postgres backup primitives via ``pg_dump`` and ``pg_restore``.

Wraps the standard PostgreSQL backup CLI tooling. Kept inside
``persistence/`` so the boundary linter's 'only persistence may import
psycopg or shell out to PG tooling' rule holds.

Subprocess invocations go through :func:`asyncio.create_subprocess_exec`
with an explicit argv list (no shell) and ``PGPASSWORD`` injected via
the child's environment so the secret never appears on argv.
"""

import asyncio
import contextlib
import os
import shutil
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final, NoReturn

from synthorg.core.domain_errors import DomainError
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


class PgToolUnavailableError(DomainError):
    """The ``pg_dump`` or ``pg_restore`` binary is not on PATH."""


class PgToolFailedError(DomainError):
    """A ``pg_dump`` / ``pg_restore`` invocation exited non-zero."""


def ensure_pg_tools_available() -> None:
    """Verify ``pg_dump`` and ``pg_restore`` are on PATH.

    Resolves both binaries at the caller's location so missing tooling
    surfaces during factory dispatch with a
    :data:`BACKUP_HANDLER_REGISTRATION_FAILED` event, instead of the
    first scheduled backup attempt.

    Raises:
        PgToolUnavailableError: Either binary is missing from PATH.
    """
    _resolve_binary(_PG_DUMP_BINARY)
    _resolve_binary(_PG_RESTORE_BINARY)


def _resolve_binary(name: str) -> str:
    """Look up *name* on PATH or raise :class:`PgToolUnavailableError`.

    Returns:
        Result of type ``str``.

    Raises:
        PgToolUnavailableError: If the underlying call raises.
    """
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

    Returns:
        Result of type ``dict[str, str]``.
    """
    env = os.environ.copy()
    env["PGPASSWORD"] = config.password.get_secret_value()
    return env


async def _terminate_proc(proc: asyncio.subprocess.Process) -> None:
    """Kill *proc* and reap it. Safe to call after natural exit."""
    if proc.returncode is None:
        proc.kill()
    await proc.wait()


async def _close_and_unlink(output_path: Path, fp: IO[bytes]) -> None:
    """Close ``fp`` and remove ``output_path``; swallow unlink ``OSError``.

    Used by the file-streaming pg_dump branch to clean up the empty or
    partially written dump artifact whenever the subprocess spawn, the
    ``communicate()`` await, or the non-zero-exit branch needs to bail
    out before a valid dump is on disk.
    """
    await asyncio.to_thread(fp.close)
    with contextlib.suppress(OSError):
        await asyncio.to_thread(output_path.unlink)


def _raise_pg_tool_failed(
    binary: str, returncode: int | None, stderr: bytes
) -> NoReturn:
    """Log ``BACKUP_COMPONENT_FAILED`` and raise ``PgToolFailedError``.

    Raises:
        PgToolFailedError: If the underlying call raises.
    """
    msg = (
        f"{binary} exited with code {returncode}: "
        f"{(stderr or b'').decode('utf-8', errors='replace').strip()}"
    )
    logger.warning(
        BACKUP_COMPONENT_FAILED,
        component=f"postgres_{Path(binary).stem}",
        returncode=returncode,
        error_type="PgToolFailedError",
        error=msg,
    )
    raise PgToolFailedError(msg)


def _raise_pg_tool_spawn_failed(binary: str, exc: OSError) -> NoReturn:
    """Normalise ``create_subprocess_exec`` ``OSError`` to a domain error.

    ``_resolve_binary`` is only a precheck; a binary can be deleted or
    lose execute permission between the check and invocation, so the
    spawn can still raise ``FileNotFoundError`` / ``PermissionError``
    at runtime. Callers expect only :class:`PgToolUnavailableError`,
    :class:`PgToolFailedError`, and :class:`TimeoutError` per the
    public docstrings -- wrap the raw OS error so the contract holds.

    Raises:
        PgToolFailedError: If the underlying call raises.
    """
    msg = f"failed to spawn {binary}: {safe_error_description(exc)}"
    logger.warning(
        BACKUP_COMPONENT_FAILED,
        component=f"postgres_{Path(binary).stem}",
        error_type="PgToolFailedError",
        error=msg,
    )
    raise PgToolFailedError(msg) from exc


async def _run_pg_tool_file(
    binary: str,
    args: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
    output_path: Path,
) -> bytes:
    """Run a PG tool with stdout streamed to ``output_path``.

    Returns the captured ``stderr`` bytes on success; raises
    :class:`PgToolFailedError` on a non-zero exit or a spawn-time
    ``OSError`` (binary disappeared, permission revoked between
    ``_resolve_binary`` and exec). Timeouts / cancellations terminate
    the process and remove the partially written dump before
    re-raising, so callers cannot mistake a failed dump for a valid
    empty one.

    Returns:
        Result of type ``bytes``.
    """
    # ``open()`` can block on slow / network-attached storage, so
    # offload to a thread to keep the event loop responsive.
    fp = await asyncio.to_thread(output_path.open, "wb")
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                *args,
                env=env,
                stdout=fp,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            await _close_and_unlink(output_path, fp)
            _raise_pg_tool_spawn_failed(binary, exc)
        except BaseException:
            await _close_and_unlink(output_path, fp)
            raise
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
        except BaseException:
            await _terminate_proc(proc)
            await _close_and_unlink(output_path, fp)
            raise
    finally:
        if not fp.closed:
            await asyncio.to_thread(fp.close)
    if proc.returncode != 0:
        with contextlib.suppress(OSError):
            await asyncio.to_thread(output_path.unlink)
        _raise_pg_tool_failed(binary, proc.returncode, stderr or b"")
    return stderr or b""


async def _run_pg_tool_buffered(
    binary: str,
    args: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
) -> tuple[bytes, bytes]:
    """Run a PG tool buffering ``(stdout, stderr)`` in memory.

    Raises :class:`PgToolFailedError` on a non-zero exit or a
    spawn-time ``OSError``; timeouts / cancellations terminate the
    process before re-raising.

    Returns:
        ``(stdout, stderr)`` captured from the subprocess.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        _raise_pg_tool_spawn_failed(binary, exc)
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except BaseException:
        await _terminate_proc(proc)
        raise
    if proc.returncode != 0:
        _raise_pg_tool_failed(binary, proc.returncode, stderr or b"")
    return stdout or b"", stderr or b""


async def _run_pg_tool(
    binary: str,
    args: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
    output_path: Path | None = None,
) -> tuple[bytes, bytes]:
    """Dispatch to file-streaming vs buffered subprocess execution.

    Raises:
        PgToolFailedError: Non-zero exit or spawn-time ``OSError``.
        TimeoutError: Subprocess did not finish within
            ``timeout_seconds``.

    Returns:
        ``(stdout, stderr)`` captured from the subprocess.
    """
    if output_path is not None:
        stderr = await _run_pg_tool_file(
            binary,
            args,
            env=env,
            timeout_seconds=timeout_seconds,
            output_path=output_path,
        )
        return b"", stderr
    return await _run_pg_tool_buffered(
        binary,
        args,
        env=env,
        timeout_seconds=timeout_seconds,
    )


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
    """Local alias preserving the redaction contract.

    Returns:
        Result of type ``str``.
    """
    return safe_error_description(exc)
