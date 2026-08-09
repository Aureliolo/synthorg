# module-kind: code
"""Running a PostgreSQL CLI tool as a child process, safely.

The substrate under :mod:`synthorg.persistence.postgres.backup_utils`: binary
resolution, the minimal child environment that carries connection settings
without handing the tool the parent's secrets, private-mode output files, and
the two execution shapes (stream stdout to a file, or buffer it). Separated from
the ``pg_dump`` / ``pg_restore`` command layer because the two answer different
questions: what to ask the tool for, against how to run one at all.

Subprocess invocations go through :func:`asyncio.create_subprocess_exec` with an
explicit argv list (no shell) and ``PGPASSWORD`` injected via the child's
environment so the secret never appears on argv. That call is unavailable on the
Windows ``SelectorEventLoop``, which is the loop psycopg's async pool requires,
so :mod:`synthorg.persistence.postgres.pg_thread_fallback` runs the same argv on
a worker thread rather than leaving backup unreachable on the loop the database
itself forces. :func:`run_pg_tool` is the one door to both.
"""

import asyncio
import contextlib
import os
import shutil
from pathlib import Path
from typing import IO, Final, NoReturn

from synthorg.core.domain_errors import DomainError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_COMPONENT_FAILED,
    BACKUP_HANDLER_REGISTRATION_FAILED,
)
from synthorg.persistence.config import PostgresConfig

logger = get_logger(__name__)


class PgToolUnavailableError(DomainError):
    """The ``pg_dump`` or ``pg_restore`` binary is not on PATH."""


class PgToolFailedError(DomainError):
    """A ``pg_dump`` / ``pg_restore`` invocation exited non-zero."""


def resolve_binary(name: str) -> str:
    """Look up *name* on PATH or raise :class:`PgToolUnavailableError`.

    Returns:
        Result of type ``str``.

    Raises:
        PgToolUnavailableError: If ``name`` is not found on PATH.
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


#: Environment keys passed through to a local-only pg tool (no DB
#: connection). Restricted to PATH, locale and the two Windows system-root
#: pointers so a subprocess that never talks to a database does not inherit
#: the parent's full environment (which may carry unrelated secrets).
#:
#: ``SYSTEMROOT`` / ``WINDIR`` are not a widening of that rule: they name
#: where Windows lives, carry no secret, and are absent on POSIX so the
#: passthrough there is unchanged. Without ``SYSTEMROOT`` a Windows child
#: cannot initialise Winsock, and libpq then reports the failure as
#: ``pg_dump: error:`` followed by nothing at all, which is a backup that
#: fails with no way to learn why.
_LOCAL_PASSTHROUGH_ENV_KEYS: Final[tuple[str, ...]] = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SYSTEMROOT",
    "WINDIR",
)

#: Owner-only mode for a dump file. The artefact is a full plaintext copy of
#: the database, credential blobs included, so it must not be readable by
#: another local user or a co-mounted sidecar.
_PRIVATE_FILE_MODE: Final[int] = 0o600


def child_env(config: PostgresConfig) -> dict[str, str]:
    """Return a minimal child-process env carrying the connection settings.

    Passes through only PATH + locale, then supplies every connection
    parameter through the ``PG*`` variables libpq reads. Inheriting the
    parent environment instead would hand ``pg_dump`` the API keys, the
    settings-encryption key and ``SYNTHORG_DATABASE_URL`` itself, none of
    which it needs.

    ``PGDATABASE`` carries the database rather than a ``--dbname=`` argument
    wherever the tool will connect on its own: libpq treats that slot as a full
    conninfo string when the value contains ``=`` or ``://``, which would let a
    config-derived name redirect the connection or downgrade ``sslmode``.
    ``pg_restore`` is the exception, since it does not connect at all without
    the flag; :class:`PostgresConfig` refuses a conninfo-shaped name so naming
    it there stays safe.

    Returns:
        Mapping of the passthrough keys plus the ``PG*`` connection settings.
    """
    env = minimal_local_env()
    env["PGPASSWORD"] = config.password.get_secret_value()
    env["PGHOST"] = config.host
    env["PGPORT"] = str(config.port)
    env["PGUSER"] = config.username
    env["PGDATABASE"] = config.database
    env["PGSSLMODE"] = config.ssl_mode
    return env


def minimal_local_env() -> dict[str, str]:
    """Return a minimal env for a local-only pg tool (no DB connection).

    Passes through only PATH + locale (:data:`_LOCAL_PASSTHROUGH_ENV_KEYS`)
    rather than the parent's full environment, so a subprocess that does
    not connect to a database cannot inherit unrelated secrets.

    Returns:
        Mapping of the present passthrough keys to their values.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key in _LOCAL_PASSTHROUGH_ENV_KEYS
    }


def open_private_binary(path: Path) -> IO[bytes]:
    """Open *path* for binary writing, readable only by the owner.

    Created through ``os.open`` with the mode supplied up front rather than
    ``Path.open`` followed by a ``chmod``: the dump is a complete plaintext
    copy of the database including every credential blob, and a
    create-then-tighten sequence leaves a window in which it is world-readable
    while ``pg_dump`` is already streaming into it.

    Returns:
        A binary file object for the newly created private file.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_FILE_MODE)
    return os.fdopen(fd, "wb")


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


def raise_pg_tool_failed(
    binary: str, returncode: int | None, stderr: bytes
) -> NoReturn:
    """Log ``BACKUP_COMPONENT_FAILED`` and raise ``PgToolFailedError``.

    Raises:
        PgToolFailedError: Always raised by this helper.
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


def raise_pg_tool_spawn_failed(binary: str, exc: OSError) -> NoReturn:
    """Normalise ``create_subprocess_exec`` ``OSError`` to a domain error.

    :func:`resolve_binary` is only a precheck; a binary can be deleted or
    lose execute permission between the check and invocation, so the
    spawn can still raise ``FileNotFoundError`` / ``PermissionError``
    at runtime. Callers expect only :class:`PgToolUnavailableError`,
    :class:`PgToolFailedError`, and :class:`TimeoutError` per the
    public docstrings -- wrap the raw OS error so the contract holds.

    Raises:
        PgToolFailedError: Always raised by this helper.
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
    :func:`resolve_binary` and exec). Timeouts / cancellations terminate
    the process and remove the partially written dump before
    re-raising, so callers cannot mistake a failed dump for a valid
    empty one.

    Returns:
        Result of type ``bytes``.
    """
    # Opened inline, not through ``to_thread``: a worker thread cannot be
    # cancelled, so awaiting the open leaves a window where a cancellation
    # returns nothing while the thread has already created the file, and the
    # handle then has no owner to close it. On Windows that unclosed handle
    # is also what stops the stray artefact being removed. Two syscalls
    # against the backup directory do not need the loop released.
    fp = open_private_binary(output_path)
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
            raise_pg_tool_spawn_failed(binary, exc)
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
        raise_pg_tool_failed(binary, proc.returncode, stderr or b"")
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
        raise_pg_tool_spawn_failed(binary, exc)
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except BaseException:
        await _terminate_proc(proc)
        raise
    if proc.returncode != 0:
        raise_pg_tool_failed(binary, proc.returncode, stderr or b"")
    return stdout or b"", stderr or b""


async def run_pg_tool(
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
    try:
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
    except NotImplementedError:
        # Imported here, not at module scope: the fallback imports this
        # module's shared pieces, so a module-level edge back would be a
        # cold-import cycle.
        from synthorg.persistence.postgres.pg_thread_fallback import (  # noqa: PLC0415
            run_pg_tool_threaded,
        )

        return await run_pg_tool_threaded(
            binary,
            args,
            env=env,
            timeout_seconds=timeout_seconds,
            output_path=output_path,
        )
