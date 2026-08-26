# module-kind: code
"""What a decomposition may do about a subtask that is too big for one agent.

The size signal itself lives in :mod:`.atomicity`; this is the budget around
it: whether recursion is switched on at all, what the thresholds currently are,
and whether the depth the operator allowed still has room.

Resolved once per decomposition rather than per level. The thresholds decide
the SHAPE of one tree, so re-reading them mid-recursion would let a write land
between two levels and produce a tree that no single set of thresholds
explains.
"""

from dataclasses import dataclass
from typing import Final

from synthorg.core.criterion_match import matched_criteria, unique_criteria
from synthorg.core.plan_tree import SubtreeStep
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.atomicity import SubtaskAtomicityPolicy
from synthorg.engine.decomposition.context import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_SUBTASKS,
    DecompositionContext,
    depth_budget,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_CEILING_UNREADABLE,
    DECOMPOSITION_FAILED,
    DECOMPOSITION_SESSIONS_EXHAUSTED,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Mirror of ``coordination.subtask_max_artifacts``. Held here because a
#: harness runs with no settings at all, and the answer has to stand there too.
DEFAULT_SUBTASK_MAX_ARTIFACTS: Final[int] = 10

#: Mirror of ``coordination.subtask_max_criteria``, for the same reason.
DEFAULT_SUBTASK_MAX_CRITERIA: Final[int] = 10


@dataclass(slots=True)
class TreeSessionLedger:
    """How many planning sessions one decomposition may still open.

    A session per node is what recursion costs, so this is the bound in the
    unit that spends money, and it is the only one that stops GRACEFULLY: the
    wall-clock ceiling raises and discards every level already paid for, while
    running out here returns the tree as far as it got and leaves the units it
    could not split saying so.

    Mutable by design, and the one mutable thing a decomposition carries: a
    budget spent across a recursive walk is a running total, and threading an
    immutable count back up through every level would put the same answer in
    two places.

    Attributes:
        remaining: Sessions still available to the whole tree.
        exhausted: Whether the ceiling has been reached, so the reason a unit
            went unsplit can name which backstop bound.
    """

    remaining: int
    exhausted: bool = False

    def take(self) -> bool:
        """Claim one planning session.

        Returns:
            ``True`` when a session was available, ``False`` once the whole
            tree's budget is spent.
        """
        if self.remaining <= 0:
            # Logged where it FLIPS, not where it is read: the reason a unit
            # went unsplit names the backstop, but which node exhausted the
            # whole tree's budget is otherwise only recoverable by counting
            # unsplit reasons backwards.
            if not self.exhausted:
                logger.info(DECOMPOSITION_SESSIONS_EXHAUSTED)
            self.exhausted = True
            return False
        self.remaining -= 1
        return True


@dataclass(frozen=True)
class RecursionBudget:
    """Whether an oversized subtask may be decomposed again, and by what rule.

    Attributes:
        enabled: Whether recursion happens at all.
        policy: The size signal deciding which subtasks are oversized.
    """

    enabled: bool
    policy: SubtaskAtomicityPolicy

    def has_room(self, context: DecompositionContext) -> bool:
        """Whether one more level fits inside the operator's depth budget.

        Asked before recursing rather than caught afterwards, because every
        strategy raises ``DecompositionDepthError`` on a context at its
        ceiling, and driving one into that state to discover the ceiling would
        turn an ordinary stop into a failed decomposition.

        Args:
            context: The level being decomposed now.

        Returns:
            ``True`` when a child level would stay within ``max_depth``.
        """
        return self.enabled and context.current_depth + 1 < depth_budget(context)


def flat_budget() -> RecursionBudget:
    """The budget that never recurses.

    Returns:
        A disabled :class:`RecursionBudget` on the definitions' own defaults.
    """
    return RecursionBudget(
        enabled=False,
        policy=SubtaskAtomicityPolicy(
            max_expected_artifacts=DEFAULT_SUBTASK_MAX_ARTIFACTS,
            max_acceptance_criteria=DEFAULT_SUBTASK_MAX_CRITERIA,
        ),
    )


def stamp_objective_criteria(
    task: Task, context: DecompositionContext
) -> DecompositionContext:
    """Fill *context*'s undeclared claim vocabulary from the objective.

    An ordered precedence ladder with one resolver, on the same seam as
    :func:`resolve_decomposition_bounds` and called from the same place: a
    caller that declared its own vocabulary keeps it, and an undeclared one
    takes the objective's own criteria. Every level below descends from the
    stamped value, so one tree is never planned against two vocabularies.

    DECLARED is read off which fields the caller set, not off whether the
    value is non-empty. An empty vocabulary is a decision the field's own
    semantics give a meaning to (this level advances no objective criterion
    and admits no claim at all), so reading it as "unset" hands a caller that
    asked for exactly that the objective's whole criteria list instead, and
    every level below then accepts claims the caller meant to refuse.

    Which branch supplies the vocabulary decides WHOSE criteria it is, never
    whether it is canonical: the field carries no uniqueness constraint, so a
    caller declaring ``("R01: X", "r01: x")`` hands the tree one criterion
    countable twice, exactly as an objective declaring both would. Both
    branches therefore go through :func:`unique_criteria`, which leaves an
    explicitly empty vocabulary empty.

    Args:
        task: The objective being decomposed.
        context: The root context, as the caller built it.

    Returns:
        The context with its vocabulary resolved.
    """
    declared = "objective_criteria" in context.model_fields_set
    criteria = (
        context.objective_criteria
        if declared
        else tuple(criterion.description for criterion in task.acceptance_criteria)
    )
    return context.model_copy(update={"objective_criteria": unique_criteria(criteria)})


def child_context(
    context: DecompositionContext,
    *,
    step: SubtreeStep,
    satisfied: tuple[NotBlankStr, ...],
) -> DecompositionContext:
    """Return *context* one level deeper, under *step*.

    The only place ``current_depth`` and ``address`` are written, and the only
    place ``objective_criteria`` is written once
    :func:`stamp_objective_criteria` has filled it at the root. The first was
    declared, read by three strategies and by the planning prompt, and set by
    nothing, so every decomposition the product ever ran believed it was at the
    root; the second is written here for the same reason, so a level cannot
    disagree with its parent about where in the tree it sits.

    The third is what makes the size signal self-terminating rather than
    merely documented as such: the child is answerable for exactly the criteria
    its parent unit claimed, so a unit claiming one hands down a vocabulary of
    one and its own children can claim at most that one.

    The narrowed tuple carries the OBJECTIVE's spelling rather than the claim's.
    A claim differing only in case or spacing matches, but handing the claim's
    text down would move the vocabulary one normalisation step per level until
    a deep claim named nothing at all, which is the defect this descent closes.

    Args:
        context: The level being decomposed now.
        step: The unit being descended into: its title and its position among
            its siblings at this level.
        satisfied: What the unit being descended into claimed to advance. An
            unmatched claim is not refused here: the parse boundary owns that
            refusal, and the one strategy that does not pass through it refuses
            recursion outright.

    Returns:
        The context the child level plans under.
    """
    return context.model_copy(
        update={
            "current_depth": context.current_depth + 1,
            "address": (*context.address, step),
            "objective_criteria": matched_criteria(
                satisfied, objective=context.objective_criteria
            ),
        }
    )


async def resolve_recursion_budget(
    resolver: ConfigResolverProtocol | None,
) -> RecursionBudget:
    """Read what this decomposition may do about an oversized subtask.

    Read live for the same reason the wall-clock ceiling is: an operator
    enabling recursion or moving a threshold applies to the next decomposition
    rather than the next restart.

    Only the two failures the SETTING can be wrong about fall back, on the same
    seam as :func:`ceiling_seconds` and :func:`_bound`: the key is not
    registered, or its stored value is not of the declared type. Anything else,
    a dead settings store above all, propagates. Recursion ships on, so
    swallowing a transient read plans every objective at one level for as long
    as the store stays down, which is the shape the sweep measured delivering
    nothing, and the only sign of it is one WARNING per decomposition.

    Args:
        resolver: The live settings resolver, or ``None`` in a harness.

    Returns:
        The budget, off on the definitions' own defaults when there is no
        resolver or the setting cannot answer.
    """
    if resolver is None:
        return flat_budget()
    try:
        enabled = await resolver.get_bool(
            "coordination", "recursive_decomposition_enabled"
        )
        if not enabled:
            # Short-circuited rather than read-then-discard: the thresholds
            # only shape a tree that is going to be built, and an operator who
            # has switched recursion off would otherwise pay two settings reads
            # per decomposition to answer a question already settled.
            return flat_budget()
        policy = SubtaskAtomicityPolicy(
            max_expected_artifacts=await resolver.get_int(
                "coordination", "subtask_max_artifacts"
            ),
            max_acceptance_criteria=await resolver.get_int(
                "coordination", "subtask_max_criteria"
            ),
        )
    except (SettingNotFoundError, ValueError) as exc:
        # lint-allow: swallow-ok -- a switch whose own definition cannot answer
        # is unreadable for as long as it stays that way, so retrying it per
        # decomposition buys nothing, and off is the only reading that cannot
        # spend a planning session per node on an instruction nobody gave
        logger.warning(
            DECOMPOSITION_FAILED,
            note="recursion settings unreadable; decomposition stays flat",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return flat_budget()
    return RecursionBudget(enabled=enabled, policy=policy)


async def _bound(
    resolver: ConfigResolverProtocol | None, key: str, fallback: int
) -> int:
    """Read one runaway backstop, falling back only where the setting is at fault.

    Args:
        resolver: The live settings resolver, or ``None`` in a harness.
        key: The ``coordination`` key holding the bound.
        fallback: The definition's own default.

    Returns:
        The operator's value, else *fallback*.
    """
    if resolver is None:
        return fallback
    try:
        return await resolver.get_int("coordination", key)
    except (SettingNotFoundError, ValueError) as exc:
        # lint-allow: swallow-ok -- a backstop the setting cannot answer for is
        # the definition's default by construction, and a bound still stands,
        # so the runaway this exists to catch is caught either way
        logger.warning(
            DECOMPOSITION_CEILING_UNREADABLE,
            setting=key,
            fallback=fallback,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return fallback


async def resolve_decomposition_bounds(
    context: DecompositionContext,
    resolver: ConfigResolverProtocol | None,
) -> DecompositionContext:
    """Fill *context*'s undeclared depth and width backstops from settings.

    An ordered precedence ladder with one resolver, called once at the root of
    a decomposition: a caller that declared a bound keeps it (the manual
    decomposition endpoints ask for a specific shape per request), an
    undeclared one takes the operator's setting, and a setting that cannot be
    read falls back to the definition's own default. Every level below plans
    under the stamped values, so one tree is never planned under two budgets.

    Each bound is read on its own, and only the two failures the SETTING can
    be wrong about are absorbed: a store that cannot answer at all is a
    different fault, and quietly planning an operator's eight-level tree at
    the shipped five is not a fallback, it is a different plan.

    Args:
        context: The root context, as the caller built it.
        resolver: The live settings resolver, or ``None`` in a harness.

    Returns:
        The context with both bounds resolved.
    """
    if context.max_depth is not None and context.max_subtasks is not None:
        return context
    depth = context.max_depth
    width = context.max_subtasks
    if depth is None:
        depth = await _bound(resolver, "decomposition_max_depth", DEFAULT_MAX_DEPTH)
    if width is None:
        width = await _bound(
            resolver, "decomposition_max_subtasks", DEFAULT_MAX_SUBTASKS
        )
    return context.model_copy(update={"max_depth": depth, "max_subtasks": width})


__all__ = [
    "DEFAULT_SUBTASK_MAX_ARTIFACTS",
    "DEFAULT_SUBTASK_MAX_CRITERIA",
    "RecursionBudget",
    "TreeSessionLedger",
    "child_context",
    "flat_budget",
    "resolve_decomposition_bounds",
    "resolve_recursion_budget",
    "stamp_objective_criteria",
]
