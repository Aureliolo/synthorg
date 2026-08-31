"""Tests for the background-job-watch capture in execute_tool_calls.

Sibling to ``test_loop_helpers.py`` (already at the tests module-size
cap), scoped to the one new branch this feature adds: capturing a
``shell_command(background=True)`` call's returned ``job_id`` into
``AgentContext.background_job_watch``. General ``execute_tool_calls``
coverage (no invoker, tool failure, ``load_tool`` / ``load_tool_resource``)
lives in ``test_loop_helpers.py``.
"""

from typing import override

import pytest
from pydantic import JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_tool_execution import execute_tool_calls
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
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class _ScriptedShellCommandTool(BaseTool):
    """A ``shell_command`` double returning a fixed, controllable result."""

    def __init__(self, result: ToolExecutionResult) -> None:
        super().__init__(
            name="shell_command",
            description="Test double",
            category=ToolCategory.TERMINAL,
        )
        self._result = result

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        return self._result


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, cost=0.001)


def _tool_use_response(
    *, background: bool, tool_call_id: str = "tc-1"
) -> CompletionResponse:
    arguments: dict[str, JsonValue] = {"command": "sleep 300"}
    if background:
        arguments["background"] = True
    return CompletionResponse(
        content=None,
        tool_calls=(
            ToolCall(id=tool_call_id, name="shell_command", arguments=arguments),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


def _ctx_with_user_msg(ctx: AgentContext) -> AgentContext:
    return ctx.with_message(ChatMessage(role=MessageRole.USER, content="Do something"))


def _invoker_returning(result: ToolExecutionResult) -> ToolInvoker:
    return ToolInvoker(ToolRegistry([_ScriptedShellCommandTool(result)]))


class TestBackgroundJobWatchCapture:
    async def test_captures_job_id_from_a_successful_background_call(
        self, sample_agent_context: AgentContext
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response(background=True)
        invoker = _invoker_returning(
            ToolExecutionResult(content='{"job_id": "abc"}', is_error=False)
        )
        clock = FakeClock()

        result = await execute_tool_calls(
            ctx, invoker, response, 1, [], clock=clock, watch_background_jobs=True
        )

        assert isinstance(result, AgentContext)
        record = result.background_job_watch.get("abc")
        assert record is not None
        assert record.started_watching_at == clock.now()

    @pytest.mark.parametrize(
        "content",
        [
            "boom",
            # A well-formed job_id payload must still be skipped: it is
            # ``is_error`` that gates capture, not whether the content
            # happens to parse.
            '{"job_id": "abc"}',
        ],
    )
    async def test_skips_a_failed_background_call(
        self, sample_agent_context: AgentContext, content: str
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response(background=True)
        invoker = _invoker_returning(
            ToolExecutionResult(content=content, is_error=True)
        )

        result = await execute_tool_calls(
            ctx, invoker, response, 1, [], clock=FakeClock(), watch_background_jobs=True
        )

        assert isinstance(result, AgentContext)
        assert result.background_job_watch.records == ()

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            "{}",
            '{"job_id": 42}',
            '{"job_id": ""}',
            '{"job_id": "   "}',
        ],
    )
    async def test_malformed_result_content_is_ignored(
        self, sample_agent_context: AgentContext, content: str
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response(background=True)
        invoker = _invoker_returning(
            ToolExecutionResult(content=content, is_error=False)
        )

        result = await execute_tool_calls(
            ctx, invoker, response, 1, [], clock=FakeClock(), watch_background_jobs=True
        )

        assert isinstance(result, AgentContext)
        assert result.background_job_watch.records == ()

    async def test_foreground_shell_command_never_touches_the_channel(
        self, sample_agent_context: AgentContext
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response(background=False)
        invoker = _invoker_returning(
            ToolExecutionResult(content="ordinary output", is_error=False)
        )

        result = await execute_tool_calls(
            ctx, invoker, response, 1, [], clock=FakeClock(), watch_background_jobs=True
        )

        assert isinstance(result, AgentContext)
        assert result.background_job_watch.records == ()

    async def test_watch_disabled_never_captures_even_a_valid_background_call(
        self, sample_agent_context: AgentContext
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response(background=True)
        invoker = _invoker_returning(
            ToolExecutionResult(content='{"job_id": "abc"}', is_error=False)
        )

        result = await execute_tool_calls(
            ctx, invoker, response, 1, [], clock=FakeClock()
        )

        assert isinstance(result, AgentContext)
        assert result.background_job_watch.records == ()

    async def test_reads_raw_content_not_the_fenced_tool_message(
        self, sample_agent_context: AgentContext
    ) -> None:
        """The wrapped tool-result message fences content; the job-id
        parse must read the ORIGINAL, unwrapped result instead."""
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response(background=True)
        invoker = _invoker_returning(
            ToolExecutionResult(content='{"job_id": "xyz"}', is_error=False)
        )

        result = await execute_tool_calls(
            ctx, invoker, response, 1, [], clock=FakeClock(), watch_background_jobs=True
        )

        assert isinstance(result, AgentContext)
        wrapped_message = result.conversation[-1]
        assert wrapped_message.tool_result is not None
        # The conversation message IS fenced (proves wrapping happened)...
        assert "<tool-result>" in wrapped_message.tool_result.content
        # ...yet the job id was still correctly parsed from the raw content.
        assert result.background_job_watch.get("xyz") is not None


class _ScriptedCompactContextTool(BaseTool):
    """A ``compact_context`` double, since the real tool's result isn't read."""

    def __init__(self) -> None:
        super().__init__(
            name="compact_context",
            description="Test double",
            category=ToolCategory.MEMORY,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok", is_error=False)


def _compact_context_response(
    *, strategy: str = "summarize", reason: str = "fill is high", **extra: JsonValue
) -> CompletionResponse:
    arguments: dict[str, JsonValue] = {"strategy": strategy, "reason": reason, **extra}
    return CompletionResponse(
        content=None,
        tool_calls=(ToolCall(id="tc-1", name="compact_context", arguments=arguments),),
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


class _FailingCompactContextTool(BaseTool):
    """A ``compact_context`` double that always reports failure."""

    def __init__(self) -> None:
        super().__init__(
            name="compact_context",
            description="Test double",
            category=ToolCategory.MEMORY,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(content="boom", is_error=True)


class TestCompactContextDirectiveCapture:
    """The loop records the directive from the CALL, not the tool's result."""

    async def test_records_the_request_from_the_call_arguments(
        self, sample_agent_context: AgentContext
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _compact_context_response(reason="context at 90 percent fill")
        invoker = ToolInvoker(ToolRegistry([_ScriptedCompactContextTool()]))

        result = await execute_tool_calls(ctx, invoker, response, 1, [])

        assert isinstance(result, AgentContext)
        request = result.compaction_request
        assert request is not None
        assert request.strategy == "summarize"
        assert request.reason == "context at 90 percent fill"
        assert request.preserve_markers is True

    async def test_preserve_markers_false_is_captured(
        self, sample_agent_context: AgentContext
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _compact_context_response(preserve_markers=False)
        invoker = ToolInvoker(ToolRegistry([_ScriptedCompactContextTool()]))

        result = await execute_tool_calls(ctx, invoker, response, 1, [])

        assert isinstance(result, AgentContext)
        assert result.compaction_request is not None
        assert result.compaction_request.preserve_markers is False

    async def test_a_failed_call_never_records_a_request(
        self, sample_agent_context: AgentContext
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _compact_context_response()
        invoker = ToolInvoker(ToolRegistry([_FailingCompactContextTool()]))

        result = await execute_tool_calls(ctx, invoker, response, 1, [])

        assert isinstance(result, AgentContext)
        assert result.compaction_request is None
