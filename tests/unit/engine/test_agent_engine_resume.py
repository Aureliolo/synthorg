"""Tests for AgentEngine.resume_parked_run (approval park/resume).

Covers the engine-side half of the governance resume path: a
deserialized parked ``AgentContext`` plus an injected approval
decision continues the original run to a terminal result, and a
parked context with no task is rejected.
"""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.run_result import AgentRunResult
from synthorg.providers.enums import MessageRole

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
        assert result.task_id == sample_task_with_criteria.id

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
