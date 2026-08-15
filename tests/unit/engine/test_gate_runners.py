"""What each gate's runner dispatches, and under which identity.

The runners are the seam between "who was selected" and "what actually
runs". Both hold no identity of their own, both narrow the session before
dispatching, and both answer with the pair the run committed to rather than
the one the roster carries. A bug in any of those runs every review as the
wrong agent, at the wrong tier, or records the wrong model against the
verdict, and none of it shows up as a failure.
"""

from datetime import date
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, ToolPermissions
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.role_catalog import (
    COMPLETION_REVIEWER_ROLE_NAME,
    RED_TEAM_ROLE_NAME,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Stakes
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.completion_oracle.errors import CompletionOracleDispatchError
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.runner import ReviewerAgentEngineRunner
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.prompt_result import SystemPrompt
from synthorg.engine.run_result import AgentRunResult
from synthorg.security.redteam.errors import RedTeamDispatchError
from synthorg.security.redteam.runner import AgentEngineRunner
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit

_RAN_ON = ModelConfig(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-expert-001"),
    capability="expert",
)


def _holder(role: str) -> AgentIdentity:
    """Build a broadly-granted roster holder of *role*.

    Returns:
        An identity carrying the day-job surface an operator might grant.
    """
    return AgentIdentity(
        id=as_uuid(f"holder-{role}"),
        name=NotBlankStr("Ada"),
        role=NotBlankStr(role),
        department=NotBlankStr("Quality Assurance"),
        model=ModelConfig(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-capable-001"),
            capability="capable",
        ),
        tools=ToolPermissions(
            access_level=ToolAccessLevel.ELEVATED,
            mcp_capabilities=(NotBlankStr("*"),),
        ),
        autonomy_level=AutonomyLevel.FULL,
        hiring_date=date(2026, 1, 15),
    )


def _result(bound_model: ModelConfig | None) -> AgentRunResult:
    """Build a finished run result carrying *bound_model*.

    Returns:
        An ``AgentRunResult`` shaped like a completed dispatch.
    """
    identity = _holder(COMPLETION_REVIEWER_ROLE_NAME)
    return AgentRunResult(
        execution_result=ExecutionResult(
            context=AgentContext.from_identity(identity),
            termination_reason=TerminationReason.COMPLETED,
        ),
        system_prompt=SystemPrompt(
            content="Review prompt",
            template_version="1.0",
            estimated_tokens=10,
            sections=("identity",),
            metadata={},
        ),
        duration_seconds=0.0,
        agent_id=NotBlankStr(str(identity.id)),
        bound_model=bound_model,
        currency="USD",
    )


def _engine(result: AgentRunResult | None = None) -> AgentEngine:
    """Build an engine double that records what it was dispatched.

    Returns:
        A typed ``AgentEngine`` substitute.
    """
    run = AsyncMock(return_value=result if result is not None else _result(_RAN_ON))
    return cast("AgentEngine", mock_of[AgentEngine](run=run))


def _oracle_input() -> CompletionOracleReviewInput:
    """Build a review input for the peer-review runner.

    Returns:
        A ``CompletionOracleReviewInput`` naming a project.
    """
    return CompletionOracleReviewInput(
        execution_id=NotBlankStr("exec-1"),
        task_id=NotBlankStr("task-1"),
        project_id=NotBlankStr("proj-1"),
        executor_agent_id=NotBlankStr("executor-1"),
        deliverable_content=NotBlankStr("done"),
        acceptance_criteria=(NotBlankStr("it works"),),
        stakes=Stakes.HIGH,
        estimated_complexity=Complexity.COMPLEX,
    )


def _red_team_input() -> RedTeamReviewInput:
    """Build a review input for the adversarial runner.

    Returns:
        A ``RedTeamReviewInput`` naming a project.
    """
    return RedTeamReviewInput(
        execution_id=NotBlankStr("exec-1"),
        task_id=NotBlankStr("task-1"),
        project_id=NotBlankStr("proj-1"),
        assigned_agent_id=NotBlankStr("executor-1"),
        deliverable_content=NotBlankStr("done"),
        agent_summary=NotBlankStr("shipped it"),
        acceptance_criteria=(NotBlankStr("it works"),),
        autonomy=AutonomyLevel.SUPERVISED,
        stakes=Stakes.HIGH,
        estimated_complexity=Complexity.COMPLEX,
    )


def _dispatched(engine: AgentEngine) -> tuple[AgentIdentity, Task]:
    """Return the identity and task the engine double was called with.

    Returns:
        The ``(identity, task)`` pair from the single recorded dispatch.
    """
    kwargs = engine.run.await_args.kwargs  # type: ignore[attr-defined]
    return kwargs["identity"], kwargs["task"]


class TestCompletionReviewerRunner:
    async def test_the_selected_holder_is_the_agent_dispatched(self) -> None:
        holder = _holder(COMPLETION_REVIEWER_ROLE_NAME)
        engine = _engine()

        await ReviewerAgentEngineRunner(engine=engine).run(
            review_input=_oracle_input(), reviewer=holder
        )

        identity, task = _dispatched(engine)
        assert identity.id == holder.id
        assert str(task.assigned_to) == str(holder.id)

    async def test_the_dispatched_session_is_narrowed(self) -> None:
        """A holder's day-job grants must not become the review's reach."""
        engine = _engine()

        await ReviewerAgentEngineRunner(engine=engine).run(
            review_input=_oracle_input(),
            reviewer=_holder(COMPLETION_REVIEWER_ROLE_NAME),
        )

        identity, _ = _dispatched(engine)
        assert identity.tools.access_level is ToolAccessLevel.STANDARD
        assert identity.tools.mcp_capabilities == ()
        assert identity.autonomy_level is AutonomyLevel.SUPERVISED

    async def test_the_review_runs_at_the_reviewed_work_s_bar(self) -> None:
        """Judging is as consequential as producing, so the tier travels."""
        engine = _engine()

        await ReviewerAgentEngineRunner(engine=engine).run(
            review_input=_oracle_input(),
            reviewer=_holder(COMPLETION_REVIEWER_ROLE_NAME),
        )

        _, task = _dispatched(engine)
        assert task.stakes is Stakes.HIGH
        assert task.estimated_complexity is Complexity.COMPLEX
        assert str(task.project) == "proj-1"

    async def test_the_runner_answers_with_what_ran(self) -> None:
        """Routing and the budget can move the pair after selection."""
        engine = _engine()

        ran = await ReviewerAgentEngineRunner(engine=engine).run(
            review_input=_oracle_input(),
            reviewer=_holder(COMPLETION_REVIEWER_ROLE_NAME),
        )

        assert ran == _RAN_ON

    async def test_a_review_input_naming_no_project_refuses(self) -> None:
        """Inventing a project id is what makes the failure silent."""
        engine = _engine()
        review_input = _oracle_input().model_copy(update={"project_id": None})

        with pytest.raises(CompletionOracleDispatchError, match="project"):
            await ReviewerAgentEngineRunner(engine=engine).run(
                review_input=review_input,
                reviewer=_holder(COMPLETION_REVIEWER_ROLE_NAME),
            )

        engine.run.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_an_engine_failure_becomes_a_typed_dispatch_error(self) -> None:
        engine = _engine()
        engine.run = AsyncMock(side_effect=RuntimeError("provider down"))  # type: ignore[method-assign]

        with pytest.raises(CompletionOracleDispatchError):
            await ReviewerAgentEngineRunner(engine=engine).run(
                review_input=_oracle_input(),
                reviewer=_holder(COMPLETION_REVIEWER_ROLE_NAME),
            )


class TestRedTeamRunner:
    async def test_the_selected_holder_is_the_agent_dispatched(self) -> None:
        holder = _holder(RED_TEAM_ROLE_NAME)
        engine = _engine()

        await AgentEngineRunner(engine=engine).run(
            review_input=_red_team_input(), red_teamer=holder
        )

        identity, task = _dispatched(engine)
        assert identity.id == holder.id
        assert str(task.assigned_to) == str(holder.id)

    async def test_the_dispatched_session_is_narrowed(self) -> None:
        engine = _engine()

        await AgentEngineRunner(engine=engine).run(
            review_input=_red_team_input(), red_teamer=_holder(RED_TEAM_ROLE_NAME)
        )

        identity, _ = _dispatched(engine)
        assert identity.tools.access_level is ToolAccessLevel.STANDARD
        assert identity.tools.mcp_capabilities == ()
        assert identity.autonomy_level is AutonomyLevel.SUPERVISED

    async def test_the_runner_answers_with_what_ran(self) -> None:
        engine = _engine()

        ran = await AgentEngineRunner(engine=engine).run(
            review_input=_red_team_input(), red_teamer=_holder(RED_TEAM_ROLE_NAME)
        )

        assert ran == _RAN_ON

    async def test_an_engine_failure_becomes_a_typed_dispatch_error(self) -> None:
        engine = _engine()
        engine.run = AsyncMock(side_effect=RuntimeError("provider down"))  # type: ignore[method-assign]

        with pytest.raises(RedTeamDispatchError):
            await AgentEngineRunner(engine=engine).run(
                review_input=_red_team_input(),
                red_teamer=_holder(RED_TEAM_ROLE_NAME),
            )
