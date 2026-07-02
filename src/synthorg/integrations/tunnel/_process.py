# module-kind: code
"""Shared subprocess plumbing for CLI-backed tunnel adapters.

The Cloudflare and Dev Tunnels adapters both spawn a long-running
vendor CLI and scrape its output for the public URL. This module
holds the helpers they share: spawn, wait-for-pattern with timeout,
background drain (so a full pipe never blocks the child), and
graceful terminate-then-kill.

Deliberately built on ``subprocess.Popen`` + worker threads rather
than ``asyncio``'s subprocess API: the Windows ``SelectorEventLoop``
(which the API server pins for psycopg's async pool) has no subprocess
support (``NotImplementedError``), so the tunnel must not depend on
the running loop's transport capabilities.
"""

import asyncio
import os
import re
import subprocess
import sys
import threading
from collections.abc import Mapping
from typing import IO, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import TUNNEL_ERROR

logger = get_logger(__name__)

_TERMINATE_GRACE_SECONDS: Final[float] = 10.0


def _merged_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    """Layer *env* over the process environment.

    ``subprocess`` ``env=`` replaces the child environment wholesale,
    so a bare override dict would strip PATH and friends.

    Returns:
        The merged environment, or ``None`` to inherit unchanged.
    """
    if env is None:
        return None
    return {**os.environ, **env}


def spawn_cli(
    args: list[str], *, env: Mapping[str, str] | None = None
) -> subprocess.Popen[bytes]:
    """Start a vendor CLI with piped stdout/stderr.

    Args:
        args: Binary path plus arguments.
        env: Extra environment entries layered over ``os.environ``.

    Returns:
        The child process handle (byte streams; callers decode).
    """
    creationflags = 0
    if sys.platform == "win32":
        # Never flash a console window for the child on Windows.
        creationflags = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(  # noqa: S603 -- fixed binary + args, shell=False
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
        env=_merged_env(env),
    )


def _scan_for_pattern(stream: IO[bytes], pattern: re.Pattern[str]) -> str | None:
    """Blocking line scan; runs in a worker thread.

    Returns:
        The first regex match (group 0), or ``None`` at EOF.
    """
    for raw in iter(stream.readline, b""):
        match = pattern.search(raw.decode("utf-8", errors="replace"))
        if match is not None:
            return match.group(0)
    return None


async def wait_for_pattern(
    stream: IO[bytes],
    pattern: re.Pattern[str],
    *,
    timeout_seconds: float,
) -> str | None:
    """Read lines until *pattern* matches, or the stream/timeout ends.

    On timeout the scanning thread is left blocked on ``readline``;
    the caller terminates the child, which closes the pipe and lets
    the thread exit. Never abandon the process without terminating it.

    Returns:
        The first regex match (group 0), or ``None`` when the stream
        closed or the timeout elapsed without a match.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_scan_for_pattern, stream, pattern),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return None


def spawn_drain_thread(stream: IO[bytes], *, name: str) -> threading.Thread:
    """Keep reading (and discarding) a child's output in the background.

    Without a reader the OS pipe buffer fills and the child blocks on
    its next write, wedging the tunnel mid-session. The daemon thread
    exits at pipe EOF (child exit / terminate).

    Returns:
        The started drain thread.
    """

    def _drain() -> None:
        try:
            for _ in iter(stream.readline, b""):
                pass
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.debug(
                TUNNEL_ERROR,
                phase="drain",
                stream=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    thread = threading.Thread(target=_drain, name=f"tunnel-drain-{name}", daemon=True)
    thread.start()
    return thread


def _terminate_blocking(process: subprocess.Popen[bytes]) -> None:
    """Terminate a child, escalating to kill after a grace period."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


async def terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate a child gracefully without blocking the event loop."""
    await asyncio.to_thread(_terminate_blocking, process)


async def run_cli(
    args: list[str],
    *,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str] | None:
    """Run a short-lived CLI command and capture its combined output.

    Args:
        args: Binary path plus arguments.
        timeout_seconds: Kill-and-give-up budget for the child.
        env: Extra environment entries layered over ``os.environ``.

    Returns:
        ``(returncode, text)``, or ``None`` on timeout (the child is
        killed first).
    """

    def _run() -> tuple[int, str] | None:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            completed = subprocess.run(  # noqa: S603 -- fixed binary + args, shell=False
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
                creationflags=creationflags,
                env=_merged_env(env),
            )
        except subprocess.TimeoutExpired:
            return None
        return (
            completed.returncode,
            completed.stdout.decode("utf-8", errors="replace"),
        )

    return await asyncio.to_thread(_run)
