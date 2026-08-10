"""Resource-lock lifecycle helpers for parallel agent execution.

Stateless helpers that resolve, validate, acquire, and release the
per-assignment resource locks for a :class:`ParallelExecutionGroup`.
``ParallelExecutor`` delegates its locking concern here.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.errors import ResourceConflictError
from synthorg.engine.parallel_models import ParallelExecutionGroup
from synthorg.engine.resource_lock import ResourceLock
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.parallel import (
    PARALLEL_LOCK_RELEASE_ERROR,
    PARALLEL_VALIDATION_ERROR,
)

logger = get_logger(__name__)

#: How long a group's teardown waits for its resource locks to come back.
#: Not an operator knob: it bounds an unwind that is already finishing, and a
#: deployment that needed a different value would be describing a lock
#: implementation too slow to hold a wave open in the first place. Generous
#: next to any in-process release, which is immediate.
_LOCK_RELEASE_TIMEOUT_SECONDS: Final[float] = 30.0


def resolve_lock(
    group: ParallelExecutionGroup,
    resource_lock: ResourceLock,
) -> ResourceLock | None:
    """Return the caller's lock, or ``None`` when the group needs none.

    The lock is always the caller's. Minting one here would scope it to a
    single group, whose claims are validated non-colliding before anything
    is acquired, so it could never contend and would leave two concurrent
    groups naming the same resource each holding a lock of their own.

    Args:
        group: The group about to run.
        resource_lock: The executor's lock, shared across its groups.

    Returns:
        *resource_lock*, or ``None`` when no assignment claims a resource
        and there is nothing to serialise.
    """
    if not any(a.resource_claims for a in group.assignments):
        return None
    return resource_lock


def validate_resource_claims(
    group: ParallelExecutionGroup,
) -> None:
    """Check for overlapping resource claims between assignments.

    Raises:
        ResourceConflictError: If two assignments claim the same
            resource.
    """
    seen: dict[str, str] = {}
    for assignment in group.assignments:
        for resource in assignment.resource_claims:
            if resource in seen:
                other = seen[resource]
                msg = (
                    f"Resource conflict: {resource!r} claimed by "
                    f"both agent {other!r} and {assignment.agent_id!r}"
                )
                logger.warning(
                    PARALLEL_VALIDATION_ERROR,
                    group_id=group.group_id,
                    error=msg,
                )
                raise ResourceConflictError(msg)
            seen[resource] = assignment.agent_id


async def acquire_all_locks(
    group: ParallelExecutionGroup,
    lock: ResourceLock,
) -> None:
    """Acquire resource locks for all assignments.

    Raises:
        ResourceConflictError: If any lock cannot be acquired; the
            acquired locks are released before re-raising.
    """
    for assignment in group.assignments:
        holder_id = f"{group.group_id}:{assignment.task_id}"
        for resource in assignment.resource_claims:
            acquired = await lock.acquire(
                resource,
                holder_id,
            )
            if not acquired:
                current_holder = lock.holder_of(resource)
                msg = (
                    f"Failed to acquire lock on {resource!r}: "
                    f"held by {current_holder!r}"
                )
                logger.warning(
                    PARALLEL_VALIDATION_ERROR,
                    group_id=group.group_id,
                    error=msg,
                )
                await release_all_locks(group, lock)
                raise ResourceConflictError(msg)


async def release_all_locks(
    group: ParallelExecutionGroup,
    lock: ResourceLock,
) -> None:
    """Release all resource locks for all assignments."""
    for assignment in group.assignments:
        holder_id = f"{group.group_id}:{assignment.task_id}"
        await lock.release_all(holder_id)


def _log_late_release(done: asyncio.Future[None], group_id: str) -> None:
    """Report how a release that outlived its wait finally ended.

    Attached only after the bounded wait gives up. Without it the future's
    exception is retrieved by nobody and surfaces, if at all, as an
    "exception was never retrieved" warning naming no group.

    Args:
        done: The completed release future.
        group_id: The group whose locks were being released.
    """
    if done.cancelled():
        return
    exc = done.exception()
    if exc is None:
        logger.info(
            PARALLEL_LOCK_RELEASE_ERROR,
            note="Resource-lock release completed after its wait expired",
            group_id=group_id,
        )
        return
    logger.warning(
        PARALLEL_LOCK_RELEASE_ERROR,
        note="Resource-lock release failed after its wait expired",
        group_id=group_id,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


async def release_locks_bounded(
    group: ParallelExecutionGroup,
    lock: ResourceLock,
) -> Exception | None:
    """Release *group*'s locks, shielded from cancellation but time-bounded.

    Shielded because a re-delivered cancellation lands on the first await of
    the unwind, and a claim never released blocks every later group naming
    the same resource. Bounded because shielding alone makes the wait
    uncancellable, and ``ResourceLock`` is pluggable: a remote one that
    never answers would hold the teardown open for good. Past the bound the
    release keeps running and reports where it ended.

    Args:
        group: The group whose claims are being released.
        lock: The lock holding them.

    Returns:
        The failure to attribute to the caller, or ``None`` when the locks
        came back in time.
    """
    releasing = asyncio.ensure_future(release_all_locks(group, lock))
    try:
        await asyncio.wait_for(asyncio.shield(releasing), _LOCK_RELEASE_TIMEOUT_SECONDS)
    except TimeoutError as timeout_exc:
        releasing.add_done_callback(
            lambda done: _log_late_release(done, group.group_id)
        )
        logger.warning(
            PARALLEL_LOCK_RELEASE_ERROR,
            note="Resource-lock release timed out; still running",
            group_id=group.group_id,
            timeout_seconds=_LOCK_RELEASE_TIMEOUT_SECONDS,
        )
        return timeout_exc
    except Exception as release_exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort teardown
        reraise_critical(release_exc)
        logger.warning(
            PARALLEL_LOCK_RELEASE_ERROR,
            note="Failed to release resource locks",
            group_id=group.group_id,
            error_type=type(release_exc).__name__,
            error=safe_error_description(release_exc),
        )
        return release_exc
    return None
