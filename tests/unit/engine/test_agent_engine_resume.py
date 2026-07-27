"""Tests for AgentEngine.resume_parked_run (approval park/resume).

Covers the engine-side half of the governance resume path: a
deserialized parked ``AgentContext`` plus an injected approval
decision continues the original run to a terminal result, and a
parked context with no task is rejected.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.agent_execute_request import AgentExecuteRequest
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.providers.enums import MessageRole
from tests._shared import mock_of

from .conftest import MockCompletionProvider
from .conftest import make_completion_response as _make_completion_response

pytestmark = pytest.mark.unit


_DECISION_MESSAGE = "[SYSTEM: Approval id='approval-1' was APPROVED by 'admin']"


class TestResumeParkedRun:
    """resume_parked_run continues a restored context with the decision."""

    async def test_resumes_to_terminal_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(provider=provider)
        parked = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        result = await engine.resume_parked_run(
            parked_context=parked,
            approval_id="approval-1",
            decision_message=_DECISION_MESSAGE,
        )

        assert isinstance(result, AgentRunResult)
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.agent_id == str(sample_agent_with_personality.id)
        assert result.task_id == str(sample_task_with_criteria.id)

    async def test_decision_message_injected_into_conversation(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(provider=provider)
        parked = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        await engine.resume_parked_run(
            parked_context=parked,
            approval_id="approval-1",
            decision_message=_DECISION_MESSAGE,
        )

        # The provider sees the restored conversation plus the injected
        # decision as a SYSTEM message before producing its turn.
        sent = provider.recorded_messages[-1]
        assert any(
            m.role == MessageRole.SYSTEM and _DECISION_MESSAGE in (m.content or "")
            for m in sent
        )

    async def test_budget_exhausted_during_resume_returns_terminal_result(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A budget error mid-resume awaits the handler, yielding a result.

        The ``except BudgetExhaustedError`` branch must ``await``
        ``_handle_budget_error``; without the await it returns an
        un-awaited coroutine, which fails the ``isinstance`` check.
        """
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(provider=provider)

        async def _exhaust(_request: AgentExecuteRequest) -> AgentRunResult:
            msg = "monthly hard stop crossed"
            raise BudgetExhaustedError(msg)

        monkeypatch.setattr(engine, "_execute", _exhaust)
        parked = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        result = await engine.resume_parked_run(
            parked_context=parked,
            approval_id="approval-1",
            decision_message=_DECISION_MESSAGE,
        )

        assert isinstance(result, AgentRunResult)
        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED

    async def test_awaiting_input_task_moved_to_in_progress_on_resume(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        # A clarification park leaves the task at AWAITING_INPUT; resume must
        # move it back to IN_PROGRESS before the loop drives it to completion.
        provider = mock_provider_factory([_make_completion_response()])
        paused = sample_task_with_criteria.model_copy(
            update={"status": TaskStatus.AWAITING_INPUT}
        )
        task_engine = mock_of[TaskEngine](
            get_task=AsyncMock(return_value=paused),
            transition_task=AsyncMock(return_value=(paused, TaskStatus.AWAITING_INPUT)),
        )
        engine = AgentEngine(provider=provider, task_engine=task_engine)
        parked = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        await engine.resume_parked_run(
            parked_context=parked,
            approval_id="approval-1",
            decision_message=_DECISION_MESSAGE,
        )

        task_engine.transition_task.assert_awaited_once()
        assert task_engine.transition_task.await_args.args[1] is TaskStatus.IN_PROGRESS

    async def test_in_progress_task_not_transitioned_on_resume(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        # A binary approval park leaves the task IN_PROGRESS; the resume must
        # not force a redundant AWAITING_INPUT -> IN_PROGRESS transition.
        provider = mock_provider_factory([_make_completion_response()])
        running = sample_task_with_criteria.model_copy(
            update={"status": TaskStatus.IN_PROGRESS}
        )
        task_engine = mock_of[TaskEngine](
            get_task=AsyncMock(return_value=running),
            transition_task=AsyncMock(return_value=(running, TaskStatus.IN_PROGRESS)),
        )
        engine = AgentEngine(provider=provider, task_engine=task_engine)
        parked = AgentContext.from_identity(
            sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        await engine.resume_parked_run(
            parked_context=parked,
            approval_id="approval-1",
            decision_message=_DECISION_MESSAGE,
        )

        task_engine.transition_task.assert_not_awaited()

    async def test_taskless_parked_context_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([_make_completion_response()])
        engine = AgentEngine(provider=provider)
        # No task bound -> task_execution is None.
        parked = AgentContext.from_identity(sample_agent_with_personality)

        with pytest.raises(ExecutionStateError, match="task-bound"):
            await engine.resume_parked_run(
                parked_context=parked,
                approval_id="approval-1",
                decision_message=_DECISION_MESSAGE,
            )
