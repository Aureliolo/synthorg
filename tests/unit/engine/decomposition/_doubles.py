# module-kind: tests
"""What a recursion test needs from a planner and from settings.

Both doubles are shared rather than copied per test file. The strategy records
the whole CONTEXT it was handed, which is what lets one double serve a test
about depth and a test about the inherited vocabulary: recording only the field
one of them reads is what produced two near-identical copies.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock

from synthorg.core.task import Task
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of


@dataclass(frozen=True)
class Bounds:
    """The size and depth budget one recursion test plans under.

    Held per test file rather than shared: a test about the atomicity signal
    wants a one-artifact cap so an ordinary unit reads oversized, and a test
    about the inherited vocabulary wants a loose one so nothing splits for a
    reason it is not asking about.
    """

    ceiling: float
    artifacts: int
    criteria: int
    depth: int
    subtasks: int
    tree_sessions: int


def config_resolver(
    bounds: Bounds,
    *,
    recursion_enabled: bool = True,
    tree_sessions: int | None = None,
    bool_error: Exception | None = None,
) -> MagicMock:
    """Build a settings resolver answering every key the service reads.

    Every key, including the ones a given case does not care about: an
    unscripted one answers with the mock itself, which reaches an arithmetic
    comparison and fails as a TypeError rather than as the behaviour under
    test.

    Args:
        bounds: What this test file plans under.
        recursion_enabled: What the recursion switch answers.
        tree_sessions: Overrides *bounds* for a case about the budget itself.
        bool_error: Raised instead of answering the switch. The two faults the
            SETTING can carry leave recursion off; anything else is the store
            rather than the setting and surfaces.

    Returns:
        The scripted resolver.
    """
    resolver: MagicMock = mock_of[ConfigResolverProtocol]()
    resolver.get_float.return_value = bounds.ceiling
    if bool_error is None:
        resolver.get_bool.return_value = recursion_enabled
    else:
        resolver.get_bool.side_effect = bool_error
    resolver.get_int.side_effect = lambda _namespace, key: {
        "subtask_max_artifacts": bounds.artifacts,
        "subtask_max_criteria": bounds.criteria,
        "decomposition_max_depth": bounds.depth,
        "decomposition_max_subtasks": bounds.subtasks,
        "decomposition_tree_max_sessions": tree_sessions
        if tree_sessions is not None
        else bounds.tree_sessions,
    }[key]
    return resolver


class ScriptedStrategy:
    """Answers with a different plan per parent task, and records its contexts.

    A recursion test needs a planner that can be asked twice about two
    different tasks, which the manual strategy cannot be: it holds one plan and
    rejects any parent but its own.

    Records the whole context rather than one field of it, so a test about
    depth and a test about the inherited vocabulary read the same double.
    """

    def __init__(self, plans: dict[str, DecompositionPlan]) -> None:
        self._plans = plans
        self.seen: list[DecompositionContext] = []

    @property
    def seen_depths(self) -> list[int]:
        """The depth each call was made at, in call order.

        Returns:
            One depth per call.
        """
        return [context.current_depth for context in self.seen]

    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        """Return the plan scripted for *task*.

        Args:
            task: The parent being decomposed.
            context: What this level was handed.

        Returns:
            The scripted plan.

        Raises:
            AssertionError: The strategy was asked about a task no case
                scripted, which means the recursion walked somewhere the test
                did not intend rather than that the planner failed.
        """
        self.seen.append(context)
        plan = self._plans.get(str(task.id))
        if plan is None:
            msg = f"strategy asked for an unscripted task {task.id!r}"
            raise AssertionError(msg)
        return plan

    def plans_any_task(self) -> bool:
        """Answer for a strategy that holds a plan per parent.

        Returns:
            ``True``: it is keyed by parent, so it plans any task it was given
            a plan for, which is what a recursion test needs.
        """
        return True

    def get_strategy_name(self) -> str:
        """Name this strategy for the service's logs.

        Returns:
            The strategy name.
        """
        return "scripted"


__all__ = [
    "Bounds",
    "ScriptedStrategy",
    "config_resolver",
]
