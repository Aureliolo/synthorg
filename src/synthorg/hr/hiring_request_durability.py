# module-kind: code
"""Reading and writing the durable set of in-flight hiring requests.

The I/O half of the pipeline, kept apart from the decision flow that drives
it: an approval outliving the process that raised it is a storage property,
and the service above cares only that a request survives a restart and that a
write it depended on either landed or said so.

Both functions are bounded by the same timeout, because both are on paths that
must not hang: the read runs inside the on-startup wiring hook, and the write
runs inside a request lock the rest of the pipeline queues behind.
"""

import asyncio
from typing import Final

from synthorg.core.persistence_errors import PersistenceError
from synthorg.hr.errors import HiringError
from synthorg.hr.models import HiringRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import HR_HIRING_PERSIST_FAILED
from synthorg.persistence.hiring_request_protocol import HiringRequestRepository

logger = get_logger(__name__)

_PERSIST_TIMEOUT_SECONDS: Final[float] = 5.0
_HYDRATE_PAGE_SIZE: Final[int] = 100


async def read_all(repo: HiringRequestRepository) -> dict[str, HiringRequest]:
    """Page the whole durable set into a map keyed by request id.

    Args:
        repo: The durable store.

    Returns:
        Every persisted request, keyed by id.
    """
    loaded: dict[str, HiringRequest] = {}
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded startup pagination
    while True:
        # Bound each page read so a hung backend cannot stall the on-startup
        # wiring hook indefinitely; mirrors the write-path timeout below. A
        # timeout surfaces to the caller, where it degrades to leaving the
        # service unwired rather than hanging.
        async with asyncio.timeout(_PERSIST_TIMEOUT_SECONDS):
            batch = await repo.list_items(limit=_HYDRATE_PAGE_SIZE, offset=offset)
        for request in batch:
            loaded[str(request.id)] = request
        if len(batch) < _HYDRATE_PAGE_SIZE:
            break
        offset += _HYDRATE_PAGE_SIZE
    return loaded


async def save_request(
    repo: HiringRequestRepository,
    request: HiringRequest,
    *,
    require_persist: bool,
) -> None:
    """Persist one request, raising only when the caller cannot tolerate a loss.

    With ``require_persist`` a persistence failure raises instead of being
    swallowed, so a caller that already performed an external side effect (an
    approval-item write, an agent registration) cannot leave the request
    transition durable-less: a restart would rehydrate stale request state
    while the side effect already exists, wedging retries.

    Args:
        repo: The durable store.
        request: The request to save.
        require_persist: Whether a failure must be raised rather than logged.

    Raises:
        HiringError: When ``require_persist`` and the save failed.
    """
    try:
        async with asyncio.timeout(_PERSIST_TIMEOUT_SECONDS):
            await repo.save(request)
    except (PersistenceError, TimeoutError) as exc:
        logger.warning(
            HR_HIRING_PERSIST_FAILED,
            request_id=str(request.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        if require_persist:
            msg = f"Failed to persist hiring request {request.id!s}"
            raise HiringError(msg) from exc


__all__ = ["read_all", "save_request"]
