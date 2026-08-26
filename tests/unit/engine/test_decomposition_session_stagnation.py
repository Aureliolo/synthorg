"""The planning session must watch itself for a loop that is not progressing.

A live run spent all twelve turns of a child planning session on one read tool
that could not answer, never attempted a plan, and took the whole tree down
with it. Nothing stopped it, because the detector an operator configures was
wired into the work loop and not into this one, so the setting applied to one
of the two loops it names and said so nowhere.

These assert the wiring reaches the loop, in both directions: configured on, a
repeating session is cut short and raises the typed exhaustion its parent
absorbs; configured off, the turn cap is still the only bound.
"""

from typing import override

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.decomposition.agent_session import (
    AgentSessionDecompositionConfig,
    AgentSessionDecompositionStrategy,
)
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.errors import (
    DecompositionStagnationError,
    DecompositionTurnBudgetError,
)
from synthorg.engine.stagnation.models import (
    StagnationConfig,
    StagnationDetectionConfig,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from tests._shared import as_uuid
from tests._shared.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
    make_e2e_identity,
)

pytestmark = pytest.mark.unit

#: Enough turns that a session repeating itself would otherwise burn them all.
_MAX_TURNS = 12


class _EmptyTool(BaseTool):
    """A read tool that answers nothing, however it is asked.

    The shape the live session met: a memory search returning no results on
    every call, so rephrasing and asking again is the correct agent behaviour
    and the loop is the only thing that can notice.
    """

    def __init__(self) -> None:
        super().__init__(
            name="search_memory",
            description="Search agent memory",
            parameters_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
            category=ToolCategory.MEMORY,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="No memories found.")


class _EmptyToolProvider:
    """Grants the session the one tool that cannot answer."""

    def build_tools(
        self, *, owner_id: str, project_id: str | None
    ) -> tuple[BaseTool, ...]:
        """Return the session's planning tools.

        Returns:
            The single empty read tool.
        """
        del owner_id, project_id
        return (_EmptyTool(),)


class _UnusedFallback(DecompositionStrategy):
    """A fallback these cases must never reach."""

    @override
    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        """Fail loudly: an exhausted session must raise, not substitute.

        Raises:
            AssertionError: Always.
        """
        del task, context
        msg = "the fallback must not stand in for an exhausted session"
        raise AssertionError(msg)

    @override
    def get_strategy_name(self) -> str:
        """Return the strategy name.

        Returns:
            The name.
        """
        return "unused-fallback"

    @override
    def plans_any_task(self) -> bool:
        """Answer whether it plans an arbitrary task.

        Returns:
            ``False``.
        """
        return False


def _task() -> Task:
    """Build the objective under decomposition.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid("obj-1"),
        title="Build a Tetris web game",
        description="A playable browser Tetris.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="tetris-web",
        created_by="ceo",
    )


def _repeating_provider() -> ScriptedProvider:
    """Script a session that asks the same question until it runs out.

    Returns:
        A provider replaying one identical tool call.
    """
    return ScriptedProvider(
        [
            build_tool_call_response("search_memory", {"query": "how do we ship"})
            for _ in range(_MAX_TURNS)
        ]
    )


def _strategy(
    *,
    stagnation: StagnationDetectionConfig,
) -> AgentSessionDecompositionStrategy:
    """Build a planning strategy over the repeating provider.

    Returns:
        The strategy, granted only the tool that cannot answer.
    """
    return AgentSessionDecompositionStrategy(
        provider_selector=lambda _identity: _repeating_provider(),
        fallback=_UnusedFallback(),
        tool_provider=_EmptyToolProvider(),
        config=AgentSessionDecompositionConfig(
            max_turns=_MAX_TURNS,
            stagnation=stagnation,
        ),
    )


class TestPlanningSessionStagnation:
    async def test_a_repeating_session_is_cut_short_and_typed(self) -> None:
        strategy = _strategy(
            stagnation=StagnationDetectionConfig(
                strategy="tool_repetition",
                # Terminate on the first detection rather than injecting a
                # correction: what is asserted here is that the detector is
                # REACHED, and the correction path is the detector's own.
                tool_repetition=StagnationConfig(max_corrections=0),
            ),
        )

        with pytest.raises(DecompositionStagnationError):
            await strategy.decompose(
                _task(),
                DecompositionContext(owner_identity=make_e2e_identity()),
            )

    async def test_the_turn_cap_is_the_only_bound_when_detection_is_off(
        self,
    ) -> None:
        # The complement, so the case above cannot pass on the turn cap alone:
        # off, the same session runs every turn and exhausts differently.
        strategy = _strategy(stagnation=StagnationDetectionConfig(strategy="off"))

        with pytest.raises(DecompositionTurnBudgetError):
            await strategy.decompose(
                _task(),
                DecompositionContext(owner_identity=make_e2e_identity()),
            )
