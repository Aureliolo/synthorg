"""What makes a task row coherent, apart from what a task row contains.

Every rule here is cross-field: it reads two or more attributes and rejects a
combination the type system cannot. They live together because that is how
they are consulted, as the answer to "what makes a Task valid", and apart from
the model because the model's own file is a declaration of shape and each of
these is an argument.

Each raises ``ValueError`` on violation, which Pydantic surfaces as a
``ValidationError`` from the validator that calls it.
"""

from collections import Counter
from typing import TYPE_CHECKING

from synthorg.core.task_enums import TaskStatus

if TYPE_CHECKING:
    # A genuine cycle, and the only one this file has: ``task`` imports these
    # checks, so importing ``Task`` back at module level would not resolve.
    # Deferring is safe here because nothing in this module evaluates the
    # annotation at runtime; every function below is called with an already-
    # constructed Task from inside that model's own validator.
    from synthorg.core.task import Task

#: Statuses that name someone doing the work, so the row must name them.
#: ``AWAITING_INPUT`` is here because it pauses a task an agent is mid-
#: execution on. ``BLOCKED``, ``FAILED``, ``CANCELLED`` and ``REJECTED`` are
#: absent: each is reachable before anyone was ever assigned.
_REQUIRES_ASSIGNEE: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.COMPLETED,
        TaskStatus.AUTH_REQUIRED,
        TaskStatus.AWAITING_INPUT,
    }
)


def _duplicates(values: tuple[str, ...]) -> list[str]:
    """Return the entries appearing more than once, sorted.

    Args:
        values: The collection to inspect.

    Returns:
        Each duplicated entry once, in sorted order.
    """
    return sorted(value for value, count in Counter(values).items() if count > 1)


def check_collections(task: Task) -> None:
    """Reject a self-dependency or a repeated entry.

    Args:
        task: The task to inspect.

    Raises:
        ValueError: If the task depends on itself, or ``dependencies`` or
            ``reviewers`` repeat an entry.
    """
    if str(task.id) in task.dependencies:
        msg = f"Task {task.id!r} cannot depend on itself"
        raise ValueError(msg)
    dupes = _duplicates(task.dependencies)
    if dupes:
        msg = f"Duplicate entries in dependencies: {dupes}"
        raise ValueError(msg)
    dupes = _duplicates(task.reviewers)
    if dupes:
        msg = f"Duplicate entries in reviewers: {dupes}"
        raise ValueError(msg)


def check_delegation(task: Task) -> None:
    """Reject a self-parent, a repeated delegate, or a delegate still assigned.

    Args:
        task: The task to inspect.

    Raises:
        ValueError: If the task is its own parent, ``delegation_chain``
            repeats an entry, or ``assigned_to`` also appears in that chain.
    """
    if task.parent_task_id is not None and task.parent_task_id == str(task.id):
        msg = f"Task {task.id!r} cannot be its own parent"
        raise ValueError(msg)
    dupes = _duplicates(task.delegation_chain)
    if dupes:
        msg = f"Duplicate entries in delegation_chain: {dupes}"
        raise ValueError(msg)
    if task.assigned_to is not None and task.assigned_to in task.delegation_chain:
        msg = f"assigned_to {task.assigned_to!r} must not appear in delegation_chain"
        raise ValueError(msg)


def check_assignment_consistency(task: Task) -> None:
    """Ensure the assignee agrees with the status.

    ``CREATED`` precedes assignment, so it must carry no assignee; the
    statuses in :data:`_REQUIRES_ASSIGNEE` name work in flight and must.

    Args:
        task: The task to inspect.

    Raises:
        ValueError: If ``CREATED`` carries an assignee, or a status that
            requires one has ``assigned_to=None``.
    """
    if task.status is TaskStatus.CREATED and task.assigned_to is not None:
        msg = "assigned_to must be None when status is 'created'"
        raise ValueError(msg)
    if task.status in _REQUIRES_ASSIGNEE and task.assigned_to is None:
        msg = f"assigned_to is required when status is {task.status.value!r}"
        raise ValueError(msg)


def check_blocked_reason_pairing(task: Task) -> None:
    """Ensure a named block reason belongs to a task that is blocked.

    The reason describes the park, so it cannot outlive it. The completion
    gate skips its judge for a task blocked BY that judge and reads the reason
    to tell that apart from a task a coordination wave parked; a reason
    carried past the release it explains answers for a later, unrelated block,
    and the judge is skipped for a task nobody escalated. That is precisely the
    status-blind skip the field replaced, arriving one release later instead of
    immediately.

    Stated as an invariant rather than left to each writer because it is a
    property of the pair, and the writers are the population that would have to
    remember it. ``with_transition`` clears the field on the way out of
    BLOCKED, so the ordinary path satisfies this without anyone acting; this
    makes the rule total, covering a task built from a persisted row or a
    factory too.

    Args:
        task: The task to inspect.

    Raises:
        ValueError: If a reason is set on a task that is not blocked.
    """
    if task.blocked_reason is not None and task.status is not TaskStatus.BLOCKED:
        msg = (
            f"blocked_reason {task.blocked_reason.value!r} is set on a task "
            f"with status {task.status.value!r}; the reason names a park "
            "that is over"
        )
        raise ValueError(msg)


def check_plan_linkage(task: Task) -> None:
    """Ensure a task implementing a plan item names the plan it came from.

    Dispatch stamps both; a directly filed task carries neither. An item id
    without a plan id drops silently out of the initiative rollup's item index
    instead of failing, so it is rejected here.

    The reverse is legitimate and load-bearing: a plan owns work that
    implements no single item (the tail's integration job), which must belong
    to the initiative for teardown and queries while staying invisible to every
    derivation over plan items.

    Args:
        task: The task to inspect.

    Raises:
        ValueError: If ``plan_item_id`` is set without ``plan_id``.
    """
    if task.plan_id is None and task.plan_item_id is not None:
        msg = "plan_item_id requires the plan_id it belongs to"
        raise ValueError(msg)


__all__ = [
    "check_assignment_consistency",
    "check_blocked_reason_pairing",
    "check_collections",
    "check_delegation",
    "check_plan_linkage",
]
