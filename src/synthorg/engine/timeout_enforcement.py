"""Global gate for engine-side ``asyncio.timeout`` wrappers.

The ``engine.timeout_enforcement_enabled`` setting lets a dev operator
disable engine timeouts globally for step-through debugging.  The flag
is a **standard mutable setting** (not ``read_only_post_init``); the
module caches the resolved value purely as a hot-path optimisation so
the detector / classifier / evaluation gates can decide between a
real ``asyncio.timeout`` and a ``contextlib.nullcontext`` without
touching the resolver on every coroutine entry.  An operator change
takes effect on the next call to :func:`set_timeout_enforcement_enabled`
(currently invoked once at startup; runtime hot-reload is a follow-up).

The startup hook in :mod:`synthorg.api.lifecycle_helpers` calls
:func:`set_timeout_enforcement_enabled` once after resolving the
setting; tests and ad-hoc callers can call the same setter directly.
If the setter is never called (test harness, anonymous boot path),
the cached value defaults to ``True`` -- the safe option, so a
misconfigured deployment that never resolves the setting still
enforces timeouts and a hung detector still surfaces as
``TimeoutError``.  Production deployments should always keep
enforcement on.
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_TIMEOUT_ENFORCEMENT_SET,
)

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
    settings chain.  Emits a single INFO state-transition log when the
    cached value actually changes, so an operator can correlate later
    timeout behaviour with the toggle.
    """
    global _enforcement_enabled  # noqa: PLW0603
    previous = _enforcement_enabled
    _enforcement_enabled = value
    if previous != value:
        logger.info(
            TASK_ENGINE_TIMEOUT_ENFORCEMENT_SET,
            previous=previous,
            current=value,
        )


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
