# module-kind: code
"""Running a PostgreSQL CLI tool when the event loop cannot spawn one.

:func:`asyncio.create_subprocess_exec` raises ``NotImplementedError`` on the
Windows ``SelectorEventLoop``, which has no IOCP subprocess integration and
which psycopg's async pool requires. Without this arm the backup path would be
unreachable on exactly the loop the database itself forces, so the same argv
runs through the blocking :mod:`subprocess` API on a worker thread.

Separated from :mod:`synthorg.persistence.postgres.pg_subprocess` because the
two answer different questions: what running a tool means, against what to do
when the loop cannot run one. The observable contract is identical, including
the private output file and the three failure shapes; what differs is that a
worker thread cannot be cancelled, so stopping a run in flight has to be
arranged explicitly rather than inherited from the loop.
"""

import asyncio
import contextlib
import subprocess
import threading
from pathlib import Path
from typing import IO

from synthorg.persistence.postgres.pg_subprocess import (
    open_private_binary,
    raise_pg_tool_failed,
    raise_pg_tool_spawn_failed,
)


class _ChildHandle:
    """Lets the async side stop a run it can no longer wait for.

    Two things cross the thread boundary, and both are needed: the process,
    so it can be killed, and a completion flag, because the thread also owns
    the open dump file and removing it while that handle lives fails outright
    on Windows and silently orphans the writer on POSIX.

    ``stop`` is durable against arriving BEFORE the spawn, which is the
    likeliest window of all: the dump is created first, so a cancellation
    timed off the file's existence lands in exactly that gap.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._stopped = False
        self.finished = threading.Event()

    def publish(self, proc: subprocess.Popen[bytes]) -> bool:
        """Register the freshly spawned child.

        Args:
            proc: The child that just started.

        Returns:
            Whether it may keep running; ``False`` when a stop already
            arrived and the caller must kill it at once.
        """
        with self._lock:
            if self._stopped:
                return False
            self._proc = proc
            return True

    def stop(self) -> None:
        """Kill the running child, or refuse one about to be published."""
        with self._lock:
            self._stopped = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()


def _run_blocking(
    binary: str,
    args: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
    output_path: Path | None,
    handle: _ChildHandle,
) -> tuple[int, bytes, bytes]:
    """Run the tool with :mod:`subprocess`, blocking the calling thread.

    The file branch opens the dump through :func:`open_private_binary` for
    the same reason the loop-native path does: the artefact is a plaintext
    copy of the database and must never exist world-readable, not even for
    the window between creation and a ``chmod``.

    Args:
        binary: Absolute path to the resolved PostgreSQL CLI tool.
        args: Tool arguments, already assembled by the command layer.
        env: The minimal child environment carrying the connection settings.
        timeout_seconds: Maximum seconds to wait before killing the child.
        output_path: Where stdout is streamed, or ``None`` to buffer it.
        handle: Receives the process so the caller can stop it on
            cancellation, which cannot reach this thread.

    Returns:
        ``(returncode, stdout, stderr)``.

    Raises:
        TimeoutExpired: The tool exceeded ``timeout_seconds``; the child is
            killed and reaped before it propagates.
    """
    sink: IO[bytes] | int = (
        subprocess.PIPE if output_path is None else open_private_binary(output_path)
    )
    try:
        proc = subprocess.Popen(  # noqa: S603 -- list argv, no shell
            [binary, *args],
            env=env,
            stdout=sink,
            stderr=subprocess.PIPE,
        )
        if not handle.publish(proc):
            proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
    finally:
        if not isinstance(sink, int):
            sink.close()
        # Set last, and in a finally: it is what the async side waits on
        # before removing the dump, so it must not be reachable while this
        # thread still holds the file open.
        handle.finished.set()
    return proc.returncode, stdout or b"", stderr or b""


async def _settle_and_discard(handle: _ChildHandle, output_path: Path | None) -> None:
    """Wait for the run to finish, then remove the dump it was writing.

    In that order, and never the reverse: Windows refuses to unlink a file
    the worker thread still holds open, and POSIX would happily unlink it and
    leave ``pg_dump`` writing into a descriptor with no name. Waiting on the
    thread rather than on the child covers both, and covers the window before
    the child exists at all.

    Args:
        handle: The run to wait on; already finished is the common case.
        output_path: The partial dump to remove, or ``None`` when stdout was
            buffered and there is nothing on disk.
    """
    await asyncio.to_thread(handle.finished.wait)
    if output_path is None:
        return
    with contextlib.suppress(OSError):
        await asyncio.to_thread(output_path.unlink)


async def run_pg_tool_threaded(
    binary: str,
    args: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
    output_path: Path | None,
) -> tuple[bytes, bytes]:
    """Run a PG tool on a worker thread when the loop cannot spawn one.

    Args:
        binary: Absolute path to the resolved PostgreSQL CLI tool.
        args: Tool arguments, already assembled by the command layer.
        env: The minimal child environment carrying the connection settings.
        timeout_seconds: Maximum seconds to wait for completion.
        output_path: Where stdout is streamed, or ``None`` to buffer it.

    Returns:
        ``(stdout, stderr)`` captured from the subprocess.

    Raises:
        PgToolFailedError: Non-zero exit or a spawn-time ``OSError``.
        TimeoutError: The tool exceeded ``timeout_seconds``.
    """
    handle = _ChildHandle()
    try:
        returncode, stdout, stderr = await asyncio.to_thread(
            _run_blocking,
            binary,
            args,
            env=env,
            timeout_seconds=timeout_seconds,
            output_path=output_path,
            handle=handle,
        )
    except subprocess.TimeoutExpired as exc:
        await _settle_and_discard(handle, output_path)
        msg = f"{binary} timed out after {timeout_seconds}s"
        raise TimeoutError(msg) from exc
    except OSError as exc:
        await _settle_and_discard(handle, output_path)
        raise_pg_tool_spawn_failed(binary, exc)
    except BaseException:
        # Cancellation never reaches the worker thread, so without this the
        # child outlives the request and goes on streaming into a dump the
        # caller believes was removed. Awaited rather than shielded, exactly
        # as the loop-native branch does: these are local OS resources this
        # call owns, the child is already stopped so the wait is short, and a
        # detached cleanup is one nothing can observe finishing.
        handle.stop()
        await _settle_and_discard(handle, output_path)
        raise
    if returncode != 0:
        await _settle_and_discard(handle, output_path)
        raise_pg_tool_failed(binary, returncode, stderr)
    return stdout, stderr
