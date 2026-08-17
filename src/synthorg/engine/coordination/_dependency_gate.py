"""Whether a subtask's declared inputs actually arrived.

The DAG decides WHEN a subtask runs. Nothing decided WHETHER it should,
so a plan whose first real wave died end to end still marched through
every later wave, paying for each one, with every task failing on its own
against inputs that were never written. The edges existed, were correct,
and were ignored.

This module is the one owner of the question the edges were supposed to
ask. It is a pure rule over statuses the task engine holds, because the
engine's row is the one owner of a subtask's status and a second answer
derived from in-memory wave outcomes would be a second owner of that.
"""

from collections.abc import Iterable, Mapping
from typing import Final

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.decomposition.models import SubtaskDefinition

#: Statuses in which a dependency has not delivered and will not deliver
#: without somebody intervening. FAILED and INTERRUPTED are here alongside
#: the terminal three because a dependent started now reads the same
#: absent output either way; what separates them is whether a replan can
#: revive the dependency, which is the replan's question and not this one.
#:
#: The complement is deliberately NOT "must be COMPLETED". A subtask that
#: has produced its work sits IN_REVIEW until a gate clears it, so
#: demanding COMPLETED would make every wave wait on the review queue and
#: turn dependency ordering into a second approval gate nobody declared.
NON_DELIVERING_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
        TaskStatus.BLOCKED,
        TaskStatus.INTERRUPTED,
    }
)


def dependency_map(
    subtasks: Iterable[SubtaskDefinition],
) -> Mapping[str, tuple[str, ...]]:
    """Map each subtask id to the ids it declares it depends on.

    Args:
        subtasks: The plan's subtask definitions.

    Returns:
        A mapping from subtask id to its declared dependency ids.
    """
    return {subtask.id: subtask.dependencies for subtask in subtasks}


def unmet_dependencies(
    dependency_statuses: Mapping[str, TaskStatus | None],
) -> tuple[str, ...]:
    """Name the dependencies that have not delivered.

    Args:
        dependency_statuses: Each declared dependency id mapped to the
            status the task engine holds for it, or ``None`` when the
            engine holds no row at all.

    Returns:
        The offending dependency ids, sorted so the reason a subtask
        gives for parking is the same string on every run. Empty when
        every dependency has delivered or is still on its way.
    """
    return tuple(
        sorted(
            dependency_id
            for dependency_id, status in dependency_statuses.items()
            if status is None or status in NON_DELIVERING_STATUSES
        )
    )


def block_reason(unmet: tuple[str, ...]) -> str:
    """Phrase the park so an operator can act on it without the log.

    Args:
        unmet: The dependency ids that have not delivered.

    Returns:
        The reason string recorded on the parked subtask.
    """
    return (
        f"Not dispatched: the work it depends on did not deliver ({', '.join(unmet)})"
    )


def abandon_reason(wave_idx: int) -> str:
    """Phrase the park for a wave the run stopped before reaching.

    Args:
        wave_idx: The wave the run stopped at.

    Returns:
        The reason string recorded on each subtask that never ran.
    """
    return (
        f"Not dispatched: the run stopped at wave {wave_idx}, so the work "
        "this depends on was never attempted"
    )


__all__ = [
    "NON_DELIVERING_STATUSES",
    "abandon_reason",
    "block_reason",
    "dependency_map",
    "unmet_dependencies",
]
