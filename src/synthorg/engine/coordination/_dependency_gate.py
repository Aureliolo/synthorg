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
from dataclasses import dataclass
from typing import Final

from synthorg.core.task_enums import (
    ATTENDED_BLOCKED_REASONS,
    BlockedReason,
    TaskStatus,
)
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


#: Statuses in which a subtask has not yet had its outcome, so a wave
#: dispatching it is asking for work rather than repeating it.
#:
#: Every subtask of a plan dispatched for the first time sits at ``CREATED``,
#: so this narrows nothing on a fresh run. It matters when a run is RESUMED:
#: the waves are rebuilt from the plan's items, which say what the plan wants
#: and not what already happened, so a level whose work finished in an earlier
#: process would be dispatched a second time. ``ASSIGNED`` and ``IN_PROGRESS``
#: stay in because a wave racing another for the same subtask is a case the
#: writer already resolves by owner, and taking them out would change that.
#:
#: Everything absent has an outcome that belongs to somebody: a finished or
#: reviewed subtask has delivered, a failed or rejected one is the replan's
#: question, a parked one carries a reason naming what it waits on, and a
#: subtask waiting on a human is waiting on the human. Re-dispatching any of
#: them spends a turn budget to overwrite an answer that already exists.
AWAITS_DISPATCH_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.CREATED,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.INTERRUPTED,
    }
)


@dataclass(frozen=True, slots=True)
class DependencyState:
    """What the engine holds for one declared dependency.

    The reason travels with the status because BLOCKED is reached from
    directions that mean opposite things, and reading the status alone
    flattened them: a task parked because a completion review asked a human
    to decide read identically to one whose own inputs died, so every
    subtask below it was parked as "the work it depends on did not deliver",
    naming work that HAD delivered and was waiting on a verdict. That reason
    is a replan's input, so the successor went looking for work to redo that
    nobody had said was wrong.

    Attributes:
        status: The status the engine holds, or ``None`` when it holds no
            row at all.
        blocked_reason: Why a BLOCKED dependency is parked. ``None`` means
            the writer did not say, which is a synonym for no member.
    """

    status: TaskStatus | None
    blocked_reason: BlockedReason | None = None

    def __post_init__(self) -> None:
        """Refuse a reason with no park to explain.

        A reason names why a BLOCKED row is parked, so one carried by any
        other status describes nothing. ``awaited`` reads it against the
        status and would answer ``False``, making the contradiction silent
        rather than absent.

        Raises:
            ValueError: If a reason is set on a status that is not BLOCKED.
        """
        if self.blocked_reason is not None and self.status is not TaskStatus.BLOCKED:
            msg = (
                f"blocked_reason {self.blocked_reason.value!r} was given with "
                f"status {self.status!r}, which is not a park it can explain"
            )
            raise ValueError(msg)

    @property
    def awaited(self) -> bool:
        """Whether somebody is holding this dependency's exit.

        Returns:
            ``True`` when it is parked on a reason a person or a sweep ends,
            so it has not delivered YET and nothing about it has failed.
        """
        return (
            self.status is TaskStatus.BLOCKED
            and self.blocked_reason in ATTENDED_BLOCKED_REASONS
        )


def awaits_dispatch(status: TaskStatus | None) -> bool:
    """Whether a wave should still dispatch the subtask holding *status*.

    Args:
        status: The status the task engine holds, or ``None`` when it holds
            no row at all.

    Returns:
        ``True`` when the subtask has no outcome yet. A missing row counts,
        so a wave rebuilt against a subtask nothing filed still reaches the
        writer and fails there with the id, rather than being silently
        dropped as though its work were done.
    """
    return status is None or status in AWAITS_DISPATCH_STATUSES


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
    dependency_states: Mapping[str, DependencyState],
) -> tuple[str, ...]:
    """Name the dependencies that died and will not deliver.

    Args:
        dependency_states: Each declared dependency id mapped to what the
            task engine holds for it.

    Returns:
        The offending dependency ids, sorted so the reason a subtask
        gives for parking is the same string on every run. Empty when
        every dependency has delivered, is still on its way, or is parked
        on somebody who can still release it, which
        :func:`awaited_dependencies` answers instead.
    """
    return tuple(
        sorted(
            dependency_id
            for dependency_id, state in dependency_states.items()
            if not state.awaited
            and (state.status is None or state.status in NON_DELIVERING_STATUSES)
        )
    )


def awaited_dependencies(
    dependency_states: Mapping[str, DependencyState],
) -> tuple[str, ...]:
    """Name the dependencies parked on somebody who can still release them.

    Kept apart from :func:`unmet_dependencies` because the two lead to
    opposite treatment. A dead input means this subtask can only fail, so it
    is parked and named. An input waiting on a decision means this subtask is
    simply not ready yet: parking it would have to claim something untrue
    (its input did not fail, and the run did not stop), so it is left where
    it is and the next pass asks again.

    Args:
        dependency_states: Each declared dependency id mapped to what the
            task engine holds for it.

    Returns:
        The awaited dependency ids, sorted for a stable log line.
    """
    return tuple(
        sorted(
            dependency_id
            for dependency_id, state in dependency_states.items()
            if state.awaited
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


def unstarted_reason(wave_idx: int) -> str:
    """Phrase the park for work that merely never started.

    Says less than :func:`abandon_reason` on purpose. This subtask sits at
    the level that stopped rather than below it, so nothing is known to have
    gone wrong with its inputs, and claiming otherwise would put a dependency
    failure that never happened in front of whoever replans.

    Args:
        wave_idx: The wave the run stopped at.

    Returns:
        The reason string recorded on each subtask that never started.
    """
    return (
        f"Not dispatched: the run stopped at wave {wave_idx} before reaching "
        "this work, which is otherwise ready"
    )


def unreachable_reason() -> str:
    """Phrase the park for work no wave was ever built for.

    Distinct from every other park because nothing about this subtask failed
    and no run stopped short of it: the wave builder could not place a
    prerequisite with any agent, so it dropped that prerequisite and then
    everything standing on it. Naming a wave index would be a fiction, since
    there is no wave to name.

    Returns:
        The reason string recorded on each subtask no wave can reach.
    """
    return (
        "Not dispatched: no wave could be built for this work, because the "
        "work it depends on could not be placed with any agent"
    )


__all__ = [
    "AWAITS_DISPATCH_STATUSES",
    "NON_DELIVERING_STATUSES",
    "DependencyState",
    "abandon_reason",
    "awaited_dependencies",
    "awaits_dispatch",
    "block_reason",
    "dependency_map",
    "unmet_dependencies",
    "unreachable_reason",
    "unstarted_reason",
]
