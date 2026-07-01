# module-kind: code
"""Shared subprocess plumbing for CLI-backed tunnel adapters.

The Cloudflare and Dev Tunnels adapters both spawn a long-running
vendor CLI and scrape its output for the public URL. This module
holds the stream helpers they share: wait-for-pattern with timeout,
background drain (so a full pipe never blocks the child), and
graceful terminate-then-kill.
"""

import asyncio
import contextlib
import re
from asyncio.subprocess import Process
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import TUNNEL_ERROR

logger = get_logger(__name__)

_TERMINATE_GRACE_SECONDS: Final[float] = 10.0
_LINE_LIMIT_BYTES: Final[int] = 64 * 1024


async def wait_for_pattern(
    stream: asyncio.StreamReader,
    pattern: re.Pattern[str],
    *,
    timeout_seconds: float,
) -> str | None:
    """Read lines until *pattern* matches, or the stream/timeout ends.

    Returns:
        The first regex match (group 0), or ``None`` when the stream
        closed or the timeout elapsed without a match.
    """

    async def _scan() -> str | None:
        while True:
            line = await stream.readline()
            if not line:
                return None
            text = line.decode("utf-8", errors="replace")
            match = pattern.search(text)
            if match is not None:
                return match.group(0)

    try:
        return await asyncio.wait_for(_scan(), timeout=timeout_seconds)
    except TimeoutError:
        return None


def spawn_drain_task(
    stream: asyncio.StreamReader,
    *,
    name: str,
) -> asyncio.Task[None]:
    """Keep reading (and discarding) a child's output in the background.

    Without a reader the OS pipe buffer fills and the child blocks on
    its next write, wedging the tunnel mid-session.

    Returns:
        The drain task; cancel it after the process exits.
    """

    async def _drain() -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.debug(
                TUNNEL_ERROR,
                phase="drain",
                stream=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return asyncio.get_running_loop().create_task(_drain(), name=f"tunnel-drain-{name}")


async def terminate_process(process: Process) -> None:
    """Terminate a child gracefully, escalating to kill after a grace period."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=_TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


def stream_limit_bytes() -> int:
    """Line-buffer limit for tunnel CLI subprocess pipes.

    Returns:
        The per-line byte cap (vendor CLIs emit short lines; the cap
        only guards against a pathological unbroken stream).
    """
    return _LINE_LIMIT_BYTES
