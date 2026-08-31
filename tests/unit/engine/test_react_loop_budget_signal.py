"""Loop-level, end-to-end coverage for the turn-boundary budget signal.

Mirrors ``test_react_loop_background_job_watch.py``'s shape: the pure
predicate has its own unit tests in ``test_loop_budget_signal.py``; this
file proves ``ReactLoop.execute`` actually consults it across a real,
multi-turn run, so the acceptance bullet ("a strictly decreasing remainder,
with a terminal warning near the ceiling") is checked against the loop, not
just the function.
"""

import re
from datetime import date
from typing import override

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_budget_signal import BudgetSignalConfig
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.react_loop import ReactLoop
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


class _EchoTool(BaseTool):
    """A trivial tool double that keeps the loop going without side effects."""

    def __init__(self) -> None:
        super().__init__(
            name="echo",
            description="Test double",
            category=ToolCategory.TERMINAL,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok", is_error=False)


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid("signal-loop-agent"),
        name="Signal Loop Agent",
        role="engineer",
        department="engineering",
        model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
        hiring_date=date(2026, 1, 1),
    )


def _tool_use(
    *, input_tokens: int, output_tokens: int, call_id: str
) -> CompletionResponse:
    return CompletionResponse(
        content=None,
        tool_calls=(ToolCall(id=call_id, name="echo", arguments={}),),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(
            input_tokens=input_tokens, output_tokens=output_tokens, cost=0.0
        ),
        model="test-basic-001",
    )


def _stop(*, input_tokens: int = 5, output_tokens: int = 5) -> CompletionResponse:
    return CompletionResponse(
        content="Done.",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=input_tokens, output_tokens=output_tokens, cost=0.0
        ),
        model="test-basic-001",
    )


def _user_messages(ctx: AgentContext) -> list[str]:
    return [
        m.content or ""
        for m in ctx.conversation
        if m.role is MessageRole.USER and m.content
    ]


def _extract_remaining_tokens(line: str, *, ceiling: int = 1_000) -> int:
    """Tokens left, from the spend figure an injected budget line names."""
    match = re.search(r"Budget: ([\d,]+)/", line)
    assert match is not None, line
    spent = int(match.group(1).replace(",", ""))
    return ceiling - spent


class TestReactLoopBudgetSignal:
    """The signal fires at declared steps and escalates near the ceiling."""

    async def test_decreasing_remainder_then_terminal_warning(
        self, mock_provider_factory: type
    ) -> None:
        ctx = AgentContext.from_identity(_identity(), token_ceiling=1_000).with_message(
            ChatMessage(role=MessageRole.USER, content="Do the task.")
        )
        # Cumulative tokens after each turn: 300 (30%), 550 (55%), 820 (82%),
        # 920 (92%), 950 (95%).
        provider = mock_provider_factory(
            [
                _tool_use(input_tokens=200, output_tokens=100, call_id="tc-1"),
                _tool_use(input_tokens=150, output_tokens=100, call_id="tc-2"),
                _tool_use(input_tokens=170, output_tokens=100, call_id="tc-3"),
                _tool_use(input_tokens=70, output_tokens=30, call_id="tc-4"),
                _tool_use(input_tokens=20, output_tokens=10, call_id="tc-5"),
                _stop(),
            ]
        )
        invoker = ToolInvoker(ToolRegistry([_EchoTool()]))
        config = BudgetSignalConfig(step_percent=25, terminal_percent=90)

        loop = ReactLoop()
        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
            budget_signal_config=config,
        )

        assert result.termination_reason is TerminationReason.COMPLETED
        signal_lines = [
            content
            for content in _user_messages(result.context)
            if "token budget" in content.lower() or "ceiling" in content.lower()
        ]
        # Steps at 25%, 50%, 75%, then two terminal warnings past 90%.
        assert len(signal_lines) == 5

        step_lines = signal_lines[:3]
        assert "25%" in step_lines[0]
        assert "50%" in step_lines[1]
        assert "75%" in step_lines[2]

        # Each step line names a strictly smaller remainder than the last:
        # 700, then 450, then 180 tokens left out of the 1,000 ceiling.
        remainders = [_extract_remaining_tokens(line) for line in step_lines]
        assert remainders == [700, 450, 180]

        terminal_lines = signal_lines[3:]
        assert len(terminal_lines) == 2
        assert all("ceiling" in line.lower() for line in terminal_lines)

    async def test_no_ceiling_never_signals(self, mock_provider_factory: type) -> None:
        ctx = AgentContext.from_identity(_identity()).with_message(
            ChatMessage(role=MessageRole.USER, content="Do the task.")
        )
        provider = mock_provider_factory([_stop()])
        config = BudgetSignalConfig(step_percent=25, terminal_percent=90)

        loop = ReactLoop()
        result = await loop.execute(
            context=ctx, provider=provider, budget_signal_config=config
        )

        assert result.termination_reason is TerminationReason.COMPLETED
        assert _user_messages(result.context) == ["Do the task."]

    async def test_no_config_is_a_no_op(self, mock_provider_factory: type) -> None:
        ctx = AgentContext.from_identity(_identity(), token_ceiling=1_000).with_message(
            ChatMessage(role=MessageRole.USER, content="Do the task.")
        )
        provider = mock_provider_factory([_stop(input_tokens=900, output_tokens=50)])

        loop = ReactLoop()
        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason is TerminationReason.COMPLETED
        assert _user_messages(result.context) == ["Do the task."]
