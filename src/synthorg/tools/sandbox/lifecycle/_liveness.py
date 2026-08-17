"""Shared liveness probing and reaping for reuse strategies.

A strategy that hands a cached container back has to know the container
is still there.  Both reuse strategies ask the same question the same
way, so the question lives here once: a strategy that grew its own copy
is a strategy that can answer differently from its sibling.
"""

from collections.abc import Awaitable, Callable

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_LIFECYCLE_DESTROY_FAILED,
    SANDBOX_LIFECYCLE_STALE_EVICTED,
)
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

logger = get_logger(__name__)


async def probe_alive(
    handle: ContainerHandle,
    *,
    strategy: str,
    owner_id: str,
    alive_fn: Callable[[ContainerHandle], Awaitable[bool]],
) -> bool:
    """Ask *alive_fn* whether *handle* is still usable.

    Args:
        handle: The cached handle about to be handed back.
        strategy: Strategy name for the log line.
        owner_id: Lifecycle owner the handle is cached under.
        alive_fn: The backend's liveness probe.

    Returns:
        ``True`` only on a positive answer.  A probe that raises answers
        "not usable": reusing a container whose state could not be
        established is the failure this probe exists to prevent, and the
        cost of being wrong is one extra container rather than every
        remaining tool call the owner makes.
    """
    try:
        return await alive_fn(handle)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SANDBOX_LIFECYCLE_STALE_EVICTED,
            strategy=strategy,
            owner_id=owner_id,
            container_id=handle.container_id,
            reason="probe_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False


def log_stale(handle: ContainerHandle, *, strategy: str, owner_id: str) -> None:
    """Record that a cached container was found dead and is being dropped."""
    logger.warning(
        SANDBOX_LIFECYCLE_STALE_EVICTED,
        strategy=strategy,
        owner_id=owner_id,
        container_id=handle.container_id,
        reason="not_running",
    )


async def reap(
    handle: ContainerHandle,
    *,
    strategy: str,
    owner_id: str,
    destroy_fn: Callable[[ContainerHandle], Awaitable[None]],
) -> None:
    """Tear down an evicted handle's remains, best effort.

    Args:
        handle: The dead handle being discarded.
        strategy: Strategy name for the log line.
        owner_id: Lifecycle owner the handle was cached under.
        destroy_fn: The backend's teardown callback.
    """
    try:
        await destroy_fn(handle)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the container is already gone; this only
        # reaps its record, and failing here would deny the caller the fresh
        # container the eviction exists to give them.
        reraise_critical(exc)
        logger.warning(
            SANDBOX_LIFECYCLE_DESTROY_FAILED,
            strategy=strategy,
            owner_id=owner_id,
            container_id=handle.container_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
