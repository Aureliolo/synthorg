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
from typing import IO, Final

from synthorg.persistence.postgres.pg_subprocess import (
    open_private_binary,
    raise_pg_tool_failed,
    raise_pg_tool_spawn_failed,
)

#: How long the cleanup blocks waiting for a stopped child to exit. It has
#: just been killed, so this is slack rather than a budget; a process that has
#: not gone by now is not going to, and holding the event loop to find out is
#: worse than leaving one stray dump behind.
_CHILD_EXIT_GRACE_SECONDS: Final[float] = 5.0

#: What the worker reports when it was abandoned before it started. The async
#: side left with the cancellation that abandoned it, so nothing reads this;
#: it exists so the refusal has a shape rather than an invented exit status.
_NOT_RUN: Final[tuple[int, bytes, bytes]] = (-1, b"", b"")


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
        self._started = False
        self.finished = threading.Event()

    def begin(self) -> bool:
        """Report whether the run should start at all.

        A worker can sit queued past the cancellation that abandoned it, and
        starting then would create a dump after the cleanup that removes it
        has already run: an artefact nothing else will ever collect.

        Returns:
            Whether the run may proceed; ``False`` once a stop has arrived.
        """
        with self._lock:
            if self._stopped:
                return False
            self._started = True
            return True

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
        """Kill the running child, or refuse one about to start.

        Signals completion itself when the run had not begun: ``begin`` and
        this share the lock, so a stop that observes an unstarted run has
        already guaranteed it never will, and there is nothing left for the
        cleanup to wait on.
        """
        with self._lock:
            self._stopped = True
            proc = self._proc
            never_started = not self._started
        if never_started:
            self.finished.set()
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
    # Opened inside the guard, not while computing it: an unwritable target
    # raises here, and a raise that skips ``handle.finished`` leaves the
    # async side waiting on a flag nothing will ever set.
    sink: IO[bytes] | int = subprocess.PIPE
    try:
        if not handle.begin():
            return _NOT_RUN
        if output_path is not None:
            sink = open_private_binary(output_path)
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
        return proc.returncode, stdout or b"", stderr or b""
    finally:
        if not isinstance(sink, int):
            sink.close()
        # Set last, and in a finally: it is what the async side waits on
        # before removing the dump, so it must not be reachable while this
        # thread still holds the file open, and it must be reachable however
        # this thread leaves.
        handle.finished.set()


def _settle_and_discard(handle: _ChildHandle, output_path: Path | None) -> None:
    """Wait for the run to finish, then remove the dump it was writing.

    In that order, and never the reverse: Windows refuses to unlink a file
    the worker thread still holds open, and POSIX would happily unlink it and
    leave ``pg_dump`` writing into a descriptor with no name. Waiting on the
    thread rather than on the child covers both, and covers the window before
    the child exists at all.

    Synchronous on purpose. This runs on the cancellation path, and a
    cancelled coroutine is not promised another suspension point: an awaited
    cleanup there is one that may never run, which is how a stopped child and
    a stray dump both survive the request that owned them. Every caller
    reaches it with the child already stopped, so the wait is the few
    milliseconds it takes to exit, and it is capped so a child that will
    never exit cannot hold the loop instead.

    Args:
        handle: The run to wait on; already finished is the common case.
        output_path: The partial dump to remove, or ``None`` when stdout was
            buffered and there is nothing on disk.
    """
    handle.finished.wait(timeout=_CHILD_EXIT_GRACE_SECONDS)
    if output_path is None:
        return
    with contextlib.suppress(OSError):
        output_path.unlink()


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
        _settle_and_discard(handle, output_path)
        msg = f"{binary} timed out after {timeout_seconds}s"
        raise TimeoutError(msg) from exc
    except OSError as exc:
        _settle_and_discard(handle, output_path)
        raise_pg_tool_spawn_failed(binary, exc)
    except BaseException:
        # Cancellation never reaches the worker thread, so without this the
        # child outlives the request and goes on streaming into a dump the
        # caller believes was removed. Stopping covers the window before the
        # spawn as well; the settle that follows is synchronous because a
        # cancelled coroutine is not promised another suspension point.
        handle.stop()
        _settle_and_discard(handle, output_path)
        raise
    if returncode != 0:
        _settle_and_discard(handle, output_path)
        raise_pg_tool_failed(binary, returncode, stderr)
    return stdout, stderr
