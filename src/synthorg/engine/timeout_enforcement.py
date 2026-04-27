"""Global gate for engine-side ``asyncio.timeout`` wrappers.

The ``engine.timeout_enforcement_enabled`` setting lets a dev operator
disable engine timeouts globally for step-through debugging.  The flag
is read once at startup and cached in a module-local variable so the
hot detector / classifier / evaluation paths can decide between a real
``asyncio.timeout`` and a ``contextlib.nullcontext`` without touching
the resolver per request.

The startup hook in :mod:`synthorg.api.lifecycle_helpers` calls
:func:`set_timeout_enforcement_enabled` once after resolving the
setting; tests and ad-hoc callers can call the same setter directly.
Production deployments should always keep enforcement on -- a missing
timeout silently lets a hung detector starve the engine.
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = get_logger(__name__)

_enforcement_enabled: bool = True
"""Process-wide cache for ``engine.timeout_enforcement_enabled``.

Defaults to ``True`` (enforcement on) so the safe behavior takes
effect before the lifecycle hook overrides it -- a misconfigured
deployment that never resolves the setting still gets timeouts.
"""


def is_timeout_enforcement_enabled() -> bool:
    """Return the current cached flag value."""
    return _enforcement_enabled


def set_timeout_enforcement_enabled(*, value: bool) -> None:
    """Set the process-wide enforcement flag.

    Called from :mod:`synthorg.api.lifecycle_helpers` once at startup
    after resolving ``engine.timeout_enforcement_enabled`` through the
    settings chain.
    """
    global _enforcement_enabled  # noqa: PLW0603
    _enforcement_enabled = value


@contextlib.asynccontextmanager
async def engine_timeout(seconds: float | None) -> AsyncIterator[None]:
    """Apply ``asyncio.timeout`` only when enforcement is enabled.

    When the cached flag is ``False`` (operator opted into
    debugging-without-timeouts), this collapses to a no-op
    ``nullcontext`` so a stack-traced engine coroutine is not torn
    down mid-debug-session.  Production keeps enforcement on so hung
    coroutines surface as ``TimeoutError`` per the existing contract.

    Args:
        seconds: Timeout budget in seconds.  ``None`` short-circuits
            to the no-op path regardless of the flag (matches
            ``asyncio.timeout(None)`` semantics).
    """
    if seconds is None or not _enforcement_enabled:
        yield
        return
    async with asyncio.timeout(seconds):
        yield
