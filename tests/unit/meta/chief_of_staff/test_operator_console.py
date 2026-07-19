# module-kind: tests
"""Unit tests for the operator console service.

The console is a thin wrapper over ``AgentEngine.run_chat_action`` acting as
the shared system console identity. These tests prove: a permitted configure
turn completes with console attribution; the console operating brief rides in
the system prompt; a sensitive action parks; and the per-session budget
checker trips at the cost ceiling.
"""

from datetime import date
from typing import cast

import pytest
from pydantic import JsonValue

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.console_identity import build_console_identity
from synthorg.meta.chief_of_staff.operator_console import (
    CONSOLE_OPERATING_BRIEF,
    ConsoleTurnArgs,
    OperatorConsoleService,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ZERO_TOKEN_USAGE,
    CompletionResponse,
    ToolCall,
)
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.tools.registry import ToolRegistry
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.engine.chat_action_fakes import InMemoryParkedRepo, QueryTool

pytestmark = pytest.mark.unit

_MODEL = serialize_model_ref(
    ModelRef(provider="test-provider", model_id="test-model-001")
)
_APPROVAL_CALL = {
    "action_type": "deploy:service",
    "title": "Deploy to prod",
    "description": "Ship the release to production.",
}


def _tool_call(name: str, **arguments: object) -> CompletionResponse:
    return CompletionResponse(
        content=f"calling {name}",
        finish_reason=FinishReason.TOOL_USE,
        tool_calls=(
            ToolCall(
                id=f"tc-{name}",
                name=name,
                arguments=cast("dict[str, JsonValue]", arguments),
            ),
        ),
        usage=ZERO_TOKEN_USAGE,
        model="test-model-001",
    )


def _ctx_with_cost(cost: float) -> AgentContext:
    """A minimal AgentContext whose accumulated cost is *cost*."""
    identity = AgentIdentity(
        name="Operator Console",
        role="console",
        department="Operations",
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
    )
    ctx = AgentContext.from_identity(identity)
    usage = ZERO_TOKEN_USAGE.model_copy(update={"cost": cost})
    return ctx.model_copy(update={"accumulated_cost": usage})


def _final(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=ZERO_TOKEN_USAGE,
        model="test-model-001",
    )


def _service(
    *,
    responses: list[CompletionResponse],
    config: ChiefOfStaffConfig | None = None,
) -> tuple[OperatorConsoleService, ScriptedProvider, QueryTool]:
    provider = ScriptedProvider(responses)
    tool = QueryTool()
    engine = AgentEngine(
        provider=provider,
        tool_registry=ToolRegistry([tool]),
        approval_store=ApprovalStore(),
        parked_context_repo=InMemoryParkedRepo(),
    )
    cfg = config or ChiefOfStaffConfig(
        operator_console_enabled=True, operator_console_model=_MODEL
    )
    identity = build_console_identity(
        model_ref=_MODEL,
        autonomy_level=cfg.operator_console_autonomy_level,
        clock=FakeClock(),
    )
    assert identity is not None
    service = OperatorConsoleService(
        engine=engine, identity=identity, autonomy_resolver=None, config=cfg
    )
    return service, provider, tool


class TestConfigure:
    async def test_permitted_configure_completes_with_attribution(self) -> None:
        service, _provider, tool = _service(
            responses=[
                _tool_call("query_metrics", window="7d"),
                _final("Connected the integration and verified health."),
            ]
        )

        result = await service.configure(
            ConsoleTurnArgs(instruction="Connect GitHub and verify it.")
        )

        assert result.action.termination_reason == TerminationReason.COMPLETED
        assert not result.action.parked
        assert result.action.final_message == (
            "Connected the integration and verified health."
        )
        assert result.console_name == "Operator Console"
        assert [tc.tool_name for tc in result.action.tool_calls] == ["query_metrics"]
        assert tool.calls == [{"window": "7d"}]

    async def test_operating_brief_rides_in_system_prompt(self) -> None:
        service, provider, _tool = _service(responses=[_final("Done.")])

        await service.configure(ConsoleTurnArgs(instruction="What is connected?"))

        first_turn = provider.received_messages[0]
        system_msg = next(m for m in first_turn if m.role == MessageRole.SYSTEM)
        assert system_msg.content is not None
        assert CONSOLE_OPERATING_BRIEF in system_msg.content

    async def test_sensitive_action_parks(self) -> None:
        service, _provider, tool = _service(
            responses=[_tool_call("request_human_approval", **_APPROVAL_CALL)]
        )

        result = await service.configure(
            ConsoleTurnArgs(instruction="Deploy the release to production.")
        )

        assert result.action.parked
        assert result.action.termination_reason == TerminationReason.PARKED
        assert result.action.approval_id is not None
        # No side effect: the gated work never ran.
        assert tool.calls == []


class TestBudgetCeiling:
    def test_checker_trips_at_or_above_ceiling(self) -> None:
        service, _provider, _tool = _service(
            responses=[_final("noop")],
            config=ChiefOfStaffConfig(
                operator_console_enabled=True,
                operator_console_model=_MODEL,
                operator_console_cost_ceiling=0.5,
            ),
        )
        checker = service._build_budget_checker()

        assert checker(_ctx_with_cost(0.49)) is False
        assert checker(_ctx_with_cost(0.5)) is True
        assert checker(_ctx_with_cost(1.2)) is True
