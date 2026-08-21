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

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.decomposition.atomicity import SubtaskAtomicityPolicy
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import DECOMPOSITION_FAILED
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Mirror of ``coordination.leaf_subtask_threshold``. Held here because a
#: harness runs with no settings at all, and the answer has to stand there too.
DEFAULT_LEAF_SUBTASK_THRESHOLD: Final[int] = 1

#: Mirror of ``coordination.subtask_max_criteria``, for the same reason.
DEFAULT_SUBTASK_MAX_CRITERIA: Final[int] = 5


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
        return self.enabled and context.current_depth + 1 < context.max_depth


def flat_budget() -> RecursionBudget:
    """The budget that never recurses.

    Returns:
        A disabled :class:`RecursionBudget` on the definitions' own defaults.
    """
    return RecursionBudget(
        enabled=False,
        policy=SubtaskAtomicityPolicy(
            max_expected_artifacts=DEFAULT_LEAF_SUBTASK_THRESHOLD,
            max_acceptance_criteria=DEFAULT_SUBTASK_MAX_CRITERIA,
        ),
    )


def child_context(context: DecompositionContext) -> DecompositionContext:
    """Return *context* one level deeper.

    The only place ``current_depth`` is written. It was declared, read by three
    strategies and by the planning prompt, and set by nothing, so every
    decomposition the product ever ran believed it was at the root.

    Args:
        context: The level being decomposed now.

    Returns:
        The context the child level plans under.
    """
    return context.model_copy(update={"current_depth": context.current_depth + 1})


async def resolve_recursion_budget(
    resolver: ConfigResolverProtocol | None,
) -> RecursionBudget:
    """Read what this decomposition may do about an oversized subtask.

    Read live for the same reason the wall-clock ceiling is: an operator
    enabling recursion or moving a threshold applies to the next decomposition
    rather than the next restart.

    Args:
        resolver: The live settings resolver, or ``None`` in a harness.

    Returns:
        The budget, off on the definitions' own defaults when there is no
        resolver or it cannot answer. Failing closed means behaving exactly as
        the product did before recursion existed, which is the only safe
        reading of a switch that could not be read.
    """
    if resolver is None:
        return flat_budget()
    try:
        enabled = await resolver.get_bool(
            "coordination", "recursive_decomposition_enabled"
        )
        if not enabled:
            # Short-circuited rather than read-then-discard: the thresholds
            # only shape a tree that is going to be built, and the default
            # configuration would otherwise pay two settings reads per
            # decomposition to answer a question already settled.
            return flat_budget()
        policy = SubtaskAtomicityPolicy(
            max_expected_artifacts=await resolver.get_int(
                "coordination", "leaf_subtask_threshold"
            ),
            max_acceptance_criteria=await resolver.get_int(
                "coordination", "subtask_max_criteria"
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort settings read; an unreadable
        # switch leaves recursion off, which is what every caller had before it
        # existed, so no behaviour is degraded by the failure
        reraise_critical(exc)
        logger.warning(
            DECOMPOSITION_FAILED,
            note="recursion settings unreadable; decomposition stays flat",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return flat_budget()
    return RecursionBudget(enabled=enabled, policy=policy)


__all__ = [
    "DEFAULT_LEAF_SUBTASK_THRESHOLD",
    "DEFAULT_SUBTASK_MAX_CRITERIA",
    "RecursionBudget",
    "child_context",
    "flat_budget",
    "resolve_recursion_budget",
]
