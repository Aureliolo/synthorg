"""Tests for AgentEngine.run_chat_action / resume_parked_chat_action.

Direct chat-driven MCP acting under trust: a chat instruction runs a
short governed tool loop with NO task lifecycle. A permitted tool
executes; a sensitive action parks via the shared ``ApprovalGate``
(``source=PARKED_CONTEXT``) with no side effect, and the decision
resumes the taskless context through ``resume_parked_chat_action``.

The escalate->park->resume->complete round-trip is the load-bearing
behaviour: it proves a *taskless* ``AgentContext`` serialises through
``ParkService`` and deserialises back into the governed loop, which
``resume_parked_run`` deliberately rejects (a parked task agent must
be task-bound). The acting agent self-requests approval for the
sensitive action (``request_human_approval``); on approval it performs
the now-authorised work via a permitted tool, so "the action
completes" is literally true rather than a re-issued gated call (the
SecOps interceptor has no prior-approval recognition and would
re-escalate a re-issued autonomy-gated tool).
"""

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import AgentIdentity, ModelConfig, ToolPermissions
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.hr.seniority import SeniorityLevel
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ZERO_TOKEN_USAGE,
    ChatMessage,
    CompletionResponse,
    ToolCall,
)
from synthorg.security.timeout.park_service import ParkService
from synthorg.tools.registry import ToolRegistry

from .chat_action_fakes import InMemoryParkedRepo, QueryTool
from .conftest import MockCompletionProvider

pytestmark = pytest.mark.unit


def _acting_identity() -> AgentIdentity:
    """An agent permitted to query analytics and request human approval."""
    return AgentIdentity(
        id=uuid4(),
        name="Acting Agent",
        role="Operations Lead",
        department="Operations",
        level=SeniorityLevel.SENIOR,
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        tools=ToolPermissions(
            access_level=ToolAccessLevel.STANDARD,
            allowed=("request_human_approval",),
        ),
    )


def _tool_call(name: str, **arguments: Any) -> CompletionResponse:
    return CompletionResponse(
        content=f"calling {name}",
        finish_reason=FinishReason.TOOL_USE,
        tool_calls=(ToolCall(id=f"tc-{name}", name=name, arguments=arguments),),
        usage=ZERO_TOKEN_USAGE,
        model="test-model-001",
    )


def _final(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=ZERO_TOKEN_USAGE,
        model="test-model-001",
    )


def _build_engine(
    *,
    responses: list[CompletionResponse],
    tool: QueryTool | None = None,
) -> tuple[AgentEngine, InMemoryParkedRepo]:
    """Build an engine with a tool registry, approval store, parked repo."""
    repo = InMemoryParkedRepo()
    registry = ToolRegistry([tool] if tool is not None else [])
    engine = AgentEngine(
        provider=MockCompletionProvider(responses),
        tool_registry=registry,
        approval_store=ApprovalStore(),
        parked_context_repo=repo,  # type: ignore[arg-type]
    )
    return engine, repo


_APPROVAL_CALL = {
    "action_type": "deploy:service",
    "title": "Deploy to prod",
    "description": "Ship the release to production.",
}


class TestParkServiceTasklessRoundTrip:
    """A taskless AgentContext must serialise and deserialise intact."""

    def test_roundtrips_taskless_context(self) -> None:
        identity = _acting_identity()
        ctx = AgentContext.from_identity(identity)
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content="persona"),
        )
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.USER, content="do the thing"),
        )

        service = ParkService()
        parked = service.park(
            context=ctx,
            approval_id="appr-1",
            agent_id=str(identity.id),
            task_id=None,
        )
        restored = service.resume(parked)

        assert restored.task_execution is None
        assert parked.task_id is None
        assert restored.identity.id == identity.id
        assert [m.content for m in restored.conversation] == [
            "persona",
            "do the thing",
        ]


class TestRunChatAction:
    """run_chat_action drives the governed loop with no task."""

    async def test_permitted_action_completes(self) -> None:
        tool = QueryTool()
        engine, _ = _build_engine(
            responses=[
                _tool_call("query_metrics", window="7d"),
                _final("Revenue is up 4%."),
            ],
            tool=tool,
        )

        result = await engine.run_chat_action(
            identity=_acting_identity(),
            instruction="What is revenue doing this week?",
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert not result.parked
        assert result.final_message == "Revenue is up 4%."
        assert [tc.tool_name for tc in result.tool_calls] == ["query_metrics"]
        assert tool.calls == [{"window": "7d"}]

    async def test_instruction_is_untrusted_fenced(self) -> None:
        engine, _ = _build_engine(responses=[_final("Acknowledged.")])

        await engine.run_chat_action(
            identity=_acting_identity(),
            instruction="SECRET-INSTRUCTION-TOKEN",
        )

        provider = engine._provider
        assert isinstance(provider, MockCompletionProvider)
        first_turn = provider.recorded_messages[0]
        user_msg = next(m for m in first_turn if m.role == MessageRole.USER)
        assert user_msg.content is not None
        assert "SECRET-INSTRUCTION-TOKEN" in user_msg.content
        assert "<task-data>" in user_msg.content


class TestSensitiveActionParks:
    """A sensitive action escalates and parks with no side effect."""

    async def test_request_human_approval_parks(self) -> None:
        tool = QueryTool()
        engine, repo = _build_engine(
            responses=[_tool_call("request_human_approval", **_APPROVAL_CALL)],
            tool=tool,
        )

        result = await engine.run_chat_action(
            identity=_acting_identity(),
            instruction="Ship the release to production.",
        )

        assert result.parked
        assert result.termination_reason == TerminationReason.PARKED
        assert result.approval_id is not None
        # No side effect: the work did not run.
        assert tool.calls == []
        # The parked context was persisted (taskless) for resume.
        parked = await repo.get_by_approval(result.approval_id)
        assert parked is not None
        assert parked.task_id is None


class TestResumeParkedChatAction:
    """The taskless resume continues the action under the decision."""

    async def test_approve_resumes_and_completes_authorized_action(self) -> None:
        tool = QueryTool()
        engine, _ = _build_engine(
            responses=[
                _tool_call("request_human_approval", **_APPROVAL_CALL),
                # Resume turn: now authorised, the agent performs the work.
                _tool_call("query_metrics", window="release"),
                _final("Done -- release metrics recorded after approval."),
            ],
            tool=tool,
        )
        parked_result = await engine.run_chat_action(
            identity=_acting_identity(),
            instruction="Ship the release to production.",
        )
        assert parked_result.parked
        approval_id = parked_result.approval_id
        assert approval_id is not None

        gate = engine._approval_gate
        assert gate is not None
        resumed = await gate.resume_context(approval_id)
        assert resumed is not None
        parked_ctx, _ = resumed
        decision = ApprovalGate.build_resume_message(
            approval_id,
            approved=True,
            decided_by="operator-1",
        )

        result = await engine.resume_parked_chat_action(
            parked_context=parked_ctx,
            approval_id=approval_id,
            decision_message=decision,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.final_message == (
            "Done -- release metrics recorded after approval."
        )
        assert tool.calls == [{"window": "release"}]

    async def test_reject_resume_leaves_no_side_effect(self) -> None:
        tool = QueryTool()
        engine, _ = _build_engine(
            responses=[
                _tool_call("request_human_approval", **_APPROVAL_CALL),
                # Resume turn after REJECT: the agent stands down.
                _final("Understood, I will not proceed."),
            ],
            tool=tool,
        )
        parked_result = await engine.run_chat_action(
            identity=_acting_identity(),
            instruction="Ship the release to production.",
        )
        approval_id = parked_result.approval_id
        assert approval_id is not None

        gate = engine._approval_gate
        assert gate is not None
        resumed = await gate.resume_context(approval_id)
        assert resumed is not None
        parked_ctx, _ = resumed
        decision = ApprovalGate.build_resume_message(
            approval_id,
            approved=False,
            decided_by="operator-1",
        )

        result = await engine.resume_parked_chat_action(
            parked_context=parked_ctx,
            approval_id=approval_id,
            decision_message=decision,
        )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert tool.calls == []

    async def test_task_bound_context_rejected(
        self,
        sample_task_with_criteria: object,
    ) -> None:
        engine, _ = _build_engine(responses=[_final("noop")])
        ctx = AgentContext.from_identity(
            _acting_identity(),
            task=sample_task_with_criteria,  # type: ignore[arg-type]
        )

        with pytest.raises(ExecutionStateError, match="task-bound"):
            await engine.resume_parked_chat_action(
                parked_context=ctx,
                approval_id="appr-1",
                decision_message="[SYSTEM: approved]",
            )
