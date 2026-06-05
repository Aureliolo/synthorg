"""Resource-lock lifecycle helpers for parallel agent execution.

Stateless helpers that resolve, validate, acquire, and release the
per-assignment resource locks for a :class:`ParallelExecutionGroup`.
``ParallelExecutor`` delegates its locking concern here.
"""

from typing import TYPE_CHECKING

from synthorg.engine.errors import ResourceConflictError
from synthorg.engine.resource_lock import InMemoryResourceLock, ResourceLock
from synthorg.observability import get_logger
from synthorg.observability.events.parallel import (
    PARALLEL_VALIDATION_ERROR,
)

if TYPE_CHECKING:
    from synthorg.engine.parallel_models import ParallelExecutionGroup

logger = get_logger(__name__)


def resolve_lock(
    group: ParallelExecutionGroup,
    resource_lock: ResourceLock | None,
) -> ResourceLock | None:
    """Return the resource lock to use, or ``None`` if not needed.

    When no assignments declare resource claims, returns ``None``
    (no locking needed).  When claims exist, falls back to
    a shared ``InMemoryResourceLock()`` if no lock was injected.

    Returns:
        The injected lock, a fresh ``InMemoryResourceLock``, or
        ``None`` when the group declares no resource claims.
    """
    has_claims = any(a.resource_claims for a in group.assignments)
    if not has_claims:
        return None
    if resource_lock is not None:
        return resource_lock
    return InMemoryResourceLock()


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
