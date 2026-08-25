"""The correction loop around the last level, end to end in both strategies.

:mod:`test_atomicity_gate` covers the pure helper that phrases the correction.
These cover what the two shipped strategies DO with it: re-ask, accept a level
the planner widened, and, when the retries are spent, raise the one error class
the level above can act on rather than the generic one it must propagate.

That last distinction is why C3 shipped: three separate places promised the
condition reached the plan, and the code raised a bare ``DecompositionError``
that nothing caught, so one non-compliant unit discarded the whole tree.
"""

from typing import cast

import pytest
from pydantic import JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.agent_session_submit import (
    PlanCapture,
    SubmitDecompositionPlanTool,
)
from synthorg.engine.decomposition.atomicity import SubtaskAtomicityPolicy
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.llm import (
    LlmDecompositionConfig,
    LlmDecompositionStrategy,
)
from synthorg.engine.errors import (
    DecompositionError,
    DecompositionUnsplittableError,
)
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from tests._shared import as_uuid, sid
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit

#: One deliverable per unit, which is what makes a two-artifact unit oversized
#: and a one-artifact unit the widened answer.
_POLICY = SubtaskAtomicityPolicy(max_expected_artifacts=1, max_acceptance_criteria=5)

#: Enough attempts that a case about acceptance is not silently a case about
#: exhaustion, and few enough that the exhaustion case is quick.
_ATTEMPTS = 2


def _task() -> Task:
    """Build the objective these levels are planned for.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid("objective"),
        title=NotBlankStr("Build the thing"),
        description=NotBlankStr("Deliver a working thing"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj-1"),
        created_by=NotBlankStr("operator"),
        acceptance_criteria=(AcceptanceCriterion(description="it runs"),),
    )


def _last_level() -> DecompositionContext:
    """A context carrying the size signal, as the last level is planned under.

    Returns:
        The context.
    """
    return DecompositionContext(max_subtasks=10, max_depth=1, atomicity=_POLICY)


def _plan_args(*, artifacts_per_unit: int, units: int = 2) -> dict[str, object]:
    """Build submission arguments for a level of *units*.

    Returns:
        The arguments a planner would send.
    """
    return {
        "task_structure": "parallel",
        "coordination_topology": "auto",
        "subtasks": [
            {
                "id": f"sub-{index}",
                "title": f"Unit {index}",
                "description": f"Build unit {index}",
                "dependencies": [],
                "estimated_complexity": "medium",
                "acceptance_criteria": [f"unit {index} works"],
                "expected_artifacts": [
                    f"src/unit_{index}_{at}.py" for at in range(artifacts_per_unit)
                ],
            }
            for index in range(units)
        ],
    }


def _tool_call(arguments: dict[str, object]) -> CompletionResponse:
    """Wrap *arguments* as the planner's tool call.

    Returns:
        The response.
    """
    return CompletionResponse(
        tool_calls=(
            ToolCall(
                id="tc-1",
                name="submit_decomposition_plan",
                arguments=cast("dict[str, JsonValue]", arguments),
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=10, cost=0.0),
        model="test-model-001",
    )


def _strategy(responses: list[CompletionResponse]) -> LlmDecompositionStrategy:
    """Build the single-shot strategy over *responses*.

    Returns:
        The strategy.
    """
    return LlmDecompositionStrategy(
        provider=ScriptedProvider(responses),
        model="test-model-001",
        config=LlmDecompositionConfig(max_retries=_ATTEMPTS - 1),
    )


class TestTheSingleShotLoopReAsks:
    async def test_a_level_the_planner_widens_is_accepted(self) -> None:
        # The whole point of the correction: breadth spent where depth ran
        # out. A first submission of two-deliverable units is refused, and the
        # one-deliverable resubmission stands.
        strategy = _strategy(
            [
                _tool_call(_plan_args(artifacts_per_unit=2)),
                _tool_call(_plan_args(artifacts_per_unit=1, units=4)),
            ]
        )

        plan = await strategy.decompose(_task(), _last_level())

        assert len(plan.subtasks) == 4

    async def test_a_level_that_was_never_oversized_is_accepted_first_time(
        self,
    ) -> None:
        strategy = _strategy([_tool_call(_plan_args(artifacts_per_unit=1))])

        plan = await strategy.decompose(_task(), _last_level())

        assert len(plan.subtasks) == 2

    async def test_a_level_with_depth_below_it_is_not_corrected_at_all(self) -> None:
        # No policy on the context means a child level is still available, so
        # an oversized unit is SPLIT rather than corrected. Refusing here would
        # trade the measured mechanism for an unmeasured one.
        strategy = _strategy([_tool_call(_plan_args(artifacts_per_unit=9))])

        plan = await strategy.decompose(
            _task(), DecompositionContext(max_subtasks=10, max_depth=3)
        )

        assert len(plan.subtasks) == 2


class TestExhaustionIsTypedApart:
    async def test_a_planner_that_never_complies_raises_the_actionable_error(
        self,
    ) -> None:
        strategy = _strategy(
            [_tool_call(_plan_args(artifacts_per_unit=2)) for _ in range(_ATTEMPTS)]
        )

        with pytest.raises(DecompositionUnsplittableError):
            await strategy.decompose(_task(), _last_level())

    async def test_a_transport_failure_stays_the_error_that_must_surface(
        self,
    ) -> None:
        # Filing a provider outage as a note on one plan item hides it where
        # nobody looks, so only the size correction is typed apart.
        strategy = _strategy(
            [
                CompletionResponse(
                    content="I cannot do that",
                    finish_reason=FinishReason.STOP,
                    usage=TokenUsage(input_tokens=10, output_tokens=10, cost=0.0),
                    model="test-model-001",
                )
                for _ in range(_ATTEMPTS)
            ]
        )

        with pytest.raises(DecompositionError) as caught:
            await strategy.decompose(_task(), _last_level())

        assert not isinstance(caught.value, DecompositionUnsplittableError)

    async def test_a_level_corrected_then_mangled_is_not_read_as_declining(
        self,
    ) -> None:
        # The flag is the LAST attempt's condition, not a sticky one: a planner
        # that fixed its sizing and then returned nonsense did not decline to
        # split, and reporting it as one would park a transport fault on a plan.
        strategy = _strategy(
            [
                _tool_call(_plan_args(artifacts_per_unit=2)),
                CompletionResponse(
                    content="not a plan at all",
                    finish_reason=FinishReason.STOP,
                    usage=TokenUsage(input_tokens=10, output_tokens=10, cost=0.0),
                    model="test-model-001",
                ),
            ]
        )

        with pytest.raises(DecompositionError) as caught:
            await strategy.decompose(_task(), _last_level())

        assert not isinstance(caught.value, DecompositionUnsplittableError)


class TestTheSessionToolAnswersTheSameWay:
    def _tool(self, capture: PlanCapture) -> SubmitDecompositionPlanTool:
        """Build the submit tool held to the size signal.

        Returns:
            The tool.
        """
        return SubmitDecompositionPlanTool(
            parent_task_id=sid("objective"),
            capture=capture,
            atomicity=_POLICY,
        )

    async def test_an_oversized_level_is_refused_and_recorded_as_declining(
        self,
    ) -> None:
        capture = PlanCapture(NotBlankStr(sid("objective")))

        await self._tool(capture).execute(arguments=_plan_args(artifacts_per_unit=2))

        assert capture.plan is None
        assert capture.declined_to_split

    async def test_a_widened_level_is_accepted(self) -> None:
        capture = PlanCapture(NotBlankStr(sid("objective")))
        tool = self._tool(capture)

        await tool.execute(arguments=_plan_args(artifacts_per_unit=2))
        await tool.execute(arguments=_plan_args(artifacts_per_unit=1, units=4))

        assert capture.plan is not None
        assert len(capture.plan.subtasks) == 4

    async def test_a_later_refusal_of_another_kind_clears_the_flag(self) -> None:
        # Same reason the single-shot loop resets it: the session's LAST
        # refusal is what the level above acts on.
        capture = PlanCapture(NotBlankStr(sid("objective")))
        tool = self._tool(capture)

        await tool.execute(arguments=_plan_args(artifacts_per_unit=2))
        await tool.execute(arguments={"subtasks": "not a list"})

        assert not capture.declined_to_split
