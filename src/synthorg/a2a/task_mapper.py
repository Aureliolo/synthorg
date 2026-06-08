"""Bidirectional task state mapping between SynthOrg and A2A.

Maps ``synthorg.core.task_enums.TaskStatus`` to/from
``synthorg.a2a.models.A2ATaskState``.  The mapping is lossy in
one direction: multiple internal states map to the same A2A state
(e.g. ASSIGNED + IN_PROGRESS + IN_REVIEW all map to ``working``).
"""

from types import MappingProxyType

from synthorg.a2a.models import A2ATaskState
from synthorg.core.task_enums import TaskStatus

# Internal -> A2A (many-to-one)
_TO_A2A: MappingProxyType[TaskStatus, A2ATaskState] = MappingProxyType(
    {
        TaskStatus.CREATED: A2ATaskState.SUBMITTED,
        TaskStatus.ASSIGNED: A2ATaskState.WORKING,
        TaskStatus.IN_PROGRESS: A2ATaskState.WORKING,
        TaskStatus.IN_REVIEW: A2ATaskState.WORKING,
        TaskStatus.COMPLETED: A2ATaskState.COMPLETED,
        TaskStatus.BLOCKED: A2ATaskState.INPUT_REQUIRED,
        TaskStatus.SUSPENDED: A2ATaskState.INPUT_REQUIRED,
        TaskStatus.FAILED: A2ATaskState.FAILED,
        TaskStatus.INTERRUPTED: A2ATaskState.FAILED,
        TaskStatus.CANCELLED: A2ATaskState.CANCELED,
        TaskStatus.REJECTED: A2ATaskState.REJECTED,
        TaskStatus.AUTH_REQUIRED: A2ATaskState.AUTH_REQUIRED,
    }
)

# A2A -> Internal (one canonical mapping per A2A state)
_FROM_A2A: MappingProxyType[A2ATaskState, TaskStatus] = MappingProxyType(
    {
        A2ATaskState.SUBMITTED: TaskStatus.CREATED,
        A2ATaskState.WORKING: TaskStatus.IN_PROGRESS,
        A2ATaskState.INPUT_REQUIRED: TaskStatus.BLOCKED,
        A2ATaskState.COMPLETED: TaskStatus.COMPLETED,
        A2ATaskState.FAILED: TaskStatus.FAILED,
        A2ATaskState.CANCELED: TaskStatus.CANCELLED,
        A2ATaskState.REJECTED: TaskStatus.REJECTED,
        A2ATaskState.AUTH_REQUIRED: TaskStatus.AUTH_REQUIRED,
    }
)


def to_a2a(status: TaskStatus) -> A2ATaskState:
    """Map an internal TaskStatus to an A2A task state.

    Args:
        status: Internal task status.

    Returns:
        Corresponding A2A task state.

    Raises:
        KeyError: If the status has no mapping (should never
            happen unless TaskStatus is extended without updating
            the map).
    """
    return _TO_A2A[status]


def from_a2a(state: A2ATaskState) -> TaskStatus:
    """Map an A2A task state to an internal TaskStatus.

    The reverse mapping picks one canonical internal status for
    each A2A state.  This is lossy: ``working`` maps to
    ``IN_PROGRESS`` (not ASSIGNED or IN_REVIEW).

    Args:
        state: A2A task state.

    Returns:
        Corresponding internal task status.

    Raises:
        KeyError: If the state has no mapping.
    """
    return _FROM_A2A[state]
