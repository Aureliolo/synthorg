"""``run_pg_tool`` runs under every event loop, not only the IOCP one.

``asyncio.create_subprocess_exec`` raises ``NotImplementedError`` on the
Windows ``SelectorEventLoop``, and that is the loop psycopg's async pool
requires, so without a fallback the Postgres backup path is unreachable on
exactly the loop the database itself forces. These exercise the fallback by
making the loop-native spawn raise the way that loop does.

The child is the running interpreter rather than a real PostgreSQL binary:
what is under test is how a child is spawned, not which one, so a Python
one-liner reaches every branch without needing ``pg_dump`` on PATH.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import IO

import pytest

from synthorg.persistence.postgres import pg_subprocess, pg_thread_fallback
from synthorg.persistence.postgres.pg_subprocess import (
    PgToolFailedError,
    minimal_local_env,
    run_pg_tool,
)

pytestmark = pytest.mark.unit

_ECHO_SCRIPT = "import sys; sys.stdout.write('dumped'); sys.stderr.write('noted')"
_FAIL_SCRIPT = "import sys; sys.stderr.write('boom'); sys.exit(3)"
_SLEEP_SCRIPT = "import time; time.sleep(30)"

#: Enough environment for the child to start, and nothing else: the fallback
#: must not depend on inheriting the parent's, which is what keeps the
#: parent's secrets out of a tool that never needs them.
_CHILD_ENV: dict[str, str] = {"PATH": ""}

#: Which open belongs to the fallback. The loop-native attempt creates the
#: dump before it discovers it cannot spawn, so its open comes first.
_FALLBACK_OPEN: int = 2

#: How long a stopped child gets to exit before the test calls it leaked. The
#: script it runs sleeps far longer, so anything that returns inside this
#: window was stopped rather than left to finish.
_CHILD_EXIT_GRACE: float = 5.0


def _record_spawns(
    monkeypatch: pytest.MonkeyPatch,
) -> list[subprocess.Popen[bytes]]:
    """Collect every child the fallback spawns, so it can be checked stopped.

    Recorded where the child is handed over rather than by wrapping
    ``subprocess.Popen``: replacing that class with a function makes
    ``isinstance`` against it a ``TypeError``, which the runtime type check on
    this very hand-over would then raise, killing the run before it registers
    the child and manufacturing the leak the test is meant to detect.

    Args:
        monkeypatch: Patch scope for the recording hand-over.

    Returns:
        The list the recorder appends each spawned child to.
    """
    spawned: list[subprocess.Popen[bytes]] = []
    handle_type = pg_thread_fallback._ChildHandle
    real_publish = handle_type.publish

    def _recording_publish(handle: object, proc: subprocess.Popen[bytes]) -> bool:
        spawned.append(proc)
        return real_publish(handle, proc)  # type: ignore[arg-type]

    monkeypatch.setattr(handle_type, "publish", _recording_publish)
    return spawned


@pytest.fixture
def loop_cannot_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the loop-native spawn fail the way a SelectorEventLoop does."""

    async def _refuse(*_args: object, **_kwargs: object) -> None:
        msg = "subprocess is not supported on this event loop"
        raise NotImplementedError(msg)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _refuse)


@pytest.mark.usefixtures("loop_cannot_spawn")
class TestThreadFallback:
    async def test_buffered_output_survives_a_loop_that_cannot_spawn(self) -> None:
        stdout, stderr = await run_pg_tool(
            sys.executable,
            ["-c", _ECHO_SCRIPT],
            env=_CHILD_ENV,
            timeout_seconds=30.0,
        )

        assert stdout == b"dumped"
        assert stderr == b"noted"

    async def test_streamed_output_lands_in_the_dump_file(self, tmp_path: Path) -> None:
        """The file branch is the one backup uses; stdout must reach the dump."""
        target = tmp_path / "dump.pgc"

        _stdout, stderr = await run_pg_tool(
            sys.executable,
            ["-c", _ECHO_SCRIPT],
            env=_CHILD_ENV,
            timeout_seconds=30.0,
            output_path=target,
        )

        assert target.read_bytes() == b"dumped"
        assert stderr == b"noted"

    async def test_a_non_zero_exit_removes_the_partial_dump(
        self, tmp_path: Path
    ) -> None:
        """A failed dump left on disk is one a restore would happily read."""
        target = tmp_path / "dump.pgc"

        with pytest.raises(PgToolFailedError):
            await run_pg_tool(
                sys.executable,
                ["-c", _FAIL_SCRIPT],
                env=_CHILD_ENV,
                timeout_seconds=30.0,
                output_path=target,
            )

        assert not target.exists()

    async def test_a_missing_binary_is_a_domain_error(self) -> None:
        """The documented contract is three failure shapes; ``OSError`` is not."""
        with pytest.raises(PgToolFailedError):
            await run_pg_tool(
                "definitely-not-a-real-binary",
                [],
                env=_CHILD_ENV,
                timeout_seconds=30.0,
            )

    async def test_the_child_can_open_a_socket(self) -> None:
        """A tool that cannot reach the network fails with nothing to read.

        The minimal environment exists so a PostgreSQL tool never inherits
        the parent's secrets, but on Windows a child without ``SYSTEMROOT``
        cannot initialise Winsock, and libpq renders that as ``pg_dump:
        error:`` followed by nothing: a backup that fails with no way to
        learn why. On POSIX the key is simply absent, so nothing widens.
        """
        env = minimal_local_env()

        if os.name == "nt":
            assert env.get("SYSTEMROOT")
        assert "PATH" in env
        # Still minimal: nothing that could carry a credential rides along.
        assert not [key for key in env if key.endswith(("_KEY", "_TOKEN", "_SECRET"))]

    async def test_cancellation_stops_the_child_and_clears_the_dump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A worker thread cannot be cancelled; the child it started can.

        Without stopping it, a cancelled backup leaves ``pg_dump`` running
        and streaming into a file the caller believes was removed, and on
        Windows the removal fails outright because the child still holds it
        open. Cancellation is timed off the dump's own creation rather than
        a sleep, so the file provably existed before it provably did not.
        """
        target = tmp_path / "dump.pgc"
        opened = asyncio.Event()
        loop = asyncio.get_running_loop()
        real_open = pg_subprocess.open_private_binary
        opens = 0

        def _announce_open(path: Path) -> IO[bytes]:
            # The loop-native attempt opens the dump before it discovers it
            # cannot spawn, so the second open is the fallback's: firing on
            # the first would cancel a run that never reached the thread.
            nonlocal opens
            handle = real_open(path)
            opens += 1
            if opens == _FALLBACK_OPEN:
                loop.call_soon_threadsafe(opened.set)
            return handle

        # Both modules, because each binds the helper by name: the
        # loop-native attempt opens through one and the fallback the other.
        monkeypatch.setattr(pg_subprocess, "open_private_binary", _announce_open)
        monkeypatch.setattr(pg_thread_fallback, "open_private_binary", _announce_open)
        spawned = _record_spawns(monkeypatch)

        task = asyncio.create_task(
            run_pg_tool(
                sys.executable,
                ["-c", _SLEEP_SCRIPT],
                env=_CHILD_ENV,
                timeout_seconds=30.0,
                output_path=target,
            )
        )
        await opened.wait()
        assert target.exists()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The file going is only half of it: an orphaned pg_dump would go on
        # running for its whole dump against a database nobody is waiting on.
        # Waited rather than polled, because polling asks whether the child
        # has already been reaped, which is a different question with its own
        # race; waiting asks the one that matters, does it stop.
        assert spawned
        for child in spawned:
            assert child.wait(timeout=_CHILD_EXIT_GRACE) is not None
        assert not target.exists()

    async def test_an_unwritable_target_does_not_hang_the_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed open still has to release what the caller waits on.

        The cleanup waits for the worker thread to signal it has finished, so
        an open that raises before that signal is armed leaves the caller
        waiting on a flag nothing will ever set: a backup that never returns
        at all, which is worse than the failure it was reporting.
        """
        target = tmp_path / "dump.pgc"

        def _refuse_open(path: Path) -> IO[bytes]:
            del path
            msg = "target is not writable"
            raise PermissionError(msg)

        # Only the fallback's open: the loop-native attempt must still get far
        # enough to hand over, or the fallback is never reached.
        monkeypatch.setattr(pg_thread_fallback, "open_private_binary", _refuse_open)

        with pytest.raises(PgToolFailedError):
            await run_pg_tool(
                sys.executable,
                ["-c", _ECHO_SCRIPT],
                env=_CHILD_ENV,
                timeout_seconds=30.0,
                output_path=target,
            )

    async def test_a_timeout_is_reported_as_a_timeout(self, tmp_path: Path) -> None:
        """``subprocess`` raises its own; callers are promised ``TimeoutError``."""
        target = tmp_path / "dump.pgc"

        with pytest.raises(TimeoutError):
            await run_pg_tool(
                sys.executable,
                ["-c", _SLEEP_SCRIPT],
                env=_CHILD_ENV,
                timeout_seconds=0.5,
                output_path=target,
            )

        assert not target.exists()
