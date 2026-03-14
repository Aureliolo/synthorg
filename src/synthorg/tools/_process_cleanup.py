"""Shared subprocess transport cleanup utility.

Provides a safe helper for closing asyncio subprocess transports
to prevent ``ResourceWarning`` on Windows with ``ProactorEventLoop``.
"""

import asyncio  # noqa: TC003 — used in runtime-visible annotation

from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_SUBPROCESS_TRANSPORT_CLOSE_FAILED,
)

logger = get_logger(__name__)


def close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    """Close subprocess transport to prevent ResourceWarning on Windows.

    On Windows with ProactorEventLoop, pipe transports may not be
    closed promptly after kill+communicate, causing ResourceWarning
    at GC time.  Explicitly closing the transport avoids this.

    Uses ``getattr`` to access the CPython-internal ``_transport``
    attribute — if the attribute is absent (different runtime or
    future CPython version), this is a no-op.  Exceptions from
    ``close()`` and ``is_closing()`` are logged and suppressed so
    cleanup never masks the primary result.

    Args:
        proc: The subprocess whose transport should be closed.
    """
    transport = getattr(proc, "_transport", None)
    if transport is None:
        return
    try:
        if transport.is_closing():
            return
        transport.close()
    except Exception:
        logger.debug(
            TOOL_SUBPROCESS_TRANSPORT_CLOSE_FAILED,
            exc_info=True,
        )
