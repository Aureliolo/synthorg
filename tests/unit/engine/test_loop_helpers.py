"""Tests for extracted loop helper functions."""

from typing import override
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.errors import RunHardCeilingExceededError
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_control_helpers import (
    check_budget,
    check_shutdown,
    check_stagnation,
    invoke_compaction,
)
from synthorg.engine.loop_helpers import (
    build_result,
    call_provider,
    check_response_errors,
    get_tool_definitions,
    make_turn_record,
    response_to_message,
)
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.loop_tool_execution import (
    clear_last_turn_tool_calls,
    execute_tool_calls,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry
from tests._shared.scripted_provider import ScriptedProvider


def _usage(
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=0.001,
    )


def _stop_response(content: str = "Done.") -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


def _tool_use_response(
    tool_name: str = "echo",
    tool_call_id: str = "tc-1",
) -> CompletionResponse:
    return CompletionResponse(
        content=None,
        tool_calls=(ToolCall(id=tool_call_id, name=tool_name, arguments={}),),
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


class _StubTool(BaseTool):
    def __init__(self, name: str = "echo") -> None:
        super().__init__(
            name=name,
            description="Test tool",
            category=ToolCategory.CODE_EXECUTION,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=f"echoed: {arguments}",
            is_error=False,
        )


def _make_invoker(*tool_names: str) -> ToolInvoker:
    tools = [_StubTool(name=n) for n in tool_names]
    return ToolInvoker(ToolRegistry(tools))


def _ctx_with_user_msg(ctx: AgentContext) -> AgentContext:
    from synthorg.providers.models import ChatMessage

    msg = ChatMessage(role=MessageRole.USER, content="Do something")
    return ctx.with_message(msg)


# ── check_shutdown ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCheckShutdown:
    def test_none_checker_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        assert check_shutdown(sample_agent_context, None, []) is None

    def test_false_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        assert (
            check_shutdown(
                sample_agent_context,
                lambda: False,
                [],
            )
            is None
        )

    def test_true_returns_shutdown_result(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = check_shutdown(
            sample_agent_context,
            lambda: True,
            [],
        )
        assert result is not None
        assert result.termination_reason == TerminationReason.SHUTDOWN

    def test_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def bad() -> bool:
            msg = "broken"
            raise ValueError(msg)

        result = check_shutdown(sample_agent_context, bad, [])
        assert result is not None
        assert result.termination_reason == TerminationReason.ERROR
        assert "Shutdown checker failed" in (result.error_message or "")

    def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def oom() -> bool:
            raise MemoryError

        with pytest.raises(MemoryError):
            check_shutdown(sample_agent_context, oom, [])


# ── check_budget ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCheckBudget:
    def test_none_checker_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        assert check_budget(sample_agent_context, None, []) is None

    def test_not_exhausted_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        assert (
            check_budget(
                sample_agent_context,
                lambda _: False,
                [],
            )
            is None
        )

    def test_exhausted_returns_budget_result(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = check_budget(
            sample_agent_context,
            lambda _: True,
            [],
        )
        assert result is not None
        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED

    def test_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def bad(_: AgentContext) -> bool:
            msg = "db error"
            raise ConnectionError(msg)

        result = check_budget(sample_agent_context, bad, [])
        assert result is not None
        assert result.termination_reason == TerminationReason.ERROR

    def test_budget_exhausted_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        # A budget-policy stop (the per-run hard ceiling) must NOT be
        # swallowed as a generic ERROR: it propagates so the engine can
        # route a ceiling crossing to PARKED for operator resume.
        def ceiling(_: AgentContext) -> bool:
            msg = "crossed"
            raise RunHardCeilingExceededError(
                msg,
                ceiling_amount=1.5,
                accumulated_cost=1.5,
                currency="USD",
            )

        with pytest.raises(RunHardCeilingExceededError):
            check_budget(sample_agent_context, ceiling, [])

    def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def oom(_: AgentContext) -> bool:
            raise MemoryError

        with pytest.raises(MemoryError):
            check_budget(sample_agent_context, oom, [])

    def test_recursion_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        def recurse(_: AgentContext) -> bool:
            raise RecursionError

        with pytest.raises(RecursionError):
            check_budget(sample_agent_context, recurse, [])


# ── call_provider ───────────────────────────────────────────────────


@pytest.mark.unit
class TestCallProvider:
    async def test_success(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        expected = _stop_response("ok")
        provider = mock_provider_factory([expected])
        config = CompletionConfig(temperature=0.5)

        result = await call_provider(
            ctx,
            provider,
            "test-model",
            None,
            config,
            1,
            [],
        )
        assert isinstance(result, CompletionResponse)
        assert result.content == "ok"

    async def test_provider_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        result = await call_provider(
            ctx,
            ScriptedProvider(error=ConnectionError("connection refused")),
            "m",
            None,
            CompletionConfig(),
            1,
            [],
        )
        from synthorg.engine.loop_protocol import ExecutionResult

        assert isinstance(result, ExecutionResult)
        assert result.termination_reason == TerminationReason.ERROR

    async def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)

        with pytest.raises(MemoryError):
            await call_provider(
                ctx,
                ScriptedProvider(error=MemoryError()),
                "m",
                None,
                CompletionConfig(),
                1,
                [],
            )


# ── check_response_errors ──────────────────────────────────────────


@pytest.mark.unit
class TestCheckResponseErrors:
    def test_stop_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        response = _stop_response()
        assert (
            check_response_errors(
                sample_agent_context,
                response,
                1,
                [],
            )
            is None
        )

    def test_content_filter_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        response = CompletionResponse(
            content=None,
            finish_reason=FinishReason.CONTENT_FILTER,
            usage=_usage(),
            model="test-model-001",
        )
        result = check_response_errors(
            sample_agent_context,
            response,
            1,
            [],
        )
        assert result is not None
        assert result.termination_reason == TerminationReason.ERROR
        assert "content_filter" in (result.error_message or "")

    def test_error_finish_reason_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        response = CompletionResponse(
            content=None,
            finish_reason=FinishReason.ERROR,
            usage=_usage(),
            model="test-model-001",
        )
        result = check_response_errors(
            sample_agent_context,
            response,
            1,
            [],
        )
        assert result is not None
        assert result.termination_reason == TerminationReason.ERROR

    def test_cost_included_in_error_context(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        response = CompletionResponse(
            content=None,
            finish_reason=FinishReason.CONTENT_FILTER,
            usage=_usage(100, 50),
            model="test-model-001",
        )
        result = check_response_errors(
            sample_agent_context,
            response,
            1,
            [],
        )
        assert result is not None
        assert result.context.turn_count == 1


# ── execute_tool_calls ──────────────────────────────────────────────


@pytest.mark.unit
class TestExecuteToolCalls:
    async def test_no_invoker_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response()
        result = await execute_tool_calls(
            ctx,
            None,
            response,
            1,
            [],
        )
        from synthorg.engine.loop_protocol import ExecutionResult

        assert isinstance(result, ExecutionResult)
        assert result.termination_reason == TerminationReason.ERROR
        assert "no tool invoker" in (result.error_message or "")

    async def test_successful_tool_execution(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response("echo")
        invoker = _make_invoker("echo")

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
        )
        assert isinstance(result, AgentContext)
        # Should have tool result message appended
        last_msg = result.conversation[-1]
        assert last_msg.role == MessageRole.TOOL

    async def test_invoke_all_exception_returns_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response()
        mock_invoker = MagicMock()
        mock_invoker.invoke_all = AsyncMock(
            side_effect=RuntimeError("boom"),
        )

        result = await execute_tool_calls(
            ctx,
            mock_invoker,
            response,
            1,
            [],
        )
        from synthorg.engine.loop_protocol import ExecutionResult

        assert isinstance(result, ExecutionResult)
        assert "Tool execution failed" in (result.error_message or "")

    async def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        response = _tool_use_response()
        mock_invoker = MagicMock()
        mock_invoker.invoke_all = AsyncMock(side_effect=MemoryError)

        with pytest.raises(MemoryError):
            await execute_tool_calls(
                ctx,
                mock_invoker,
                response,
                1,
                [],
            )


# ── get_tool_definitions ────────────────────────────────────────────


@pytest.mark.unit
class TestGetToolDefinitions:
    def test_none_invoker_returns_none(self) -> None:
        assert get_tool_definitions(None) is None

    def test_empty_registry_returns_none(self) -> None:
        invoker = ToolInvoker(ToolRegistry([]))
        assert get_tool_definitions(invoker) is None

    def test_returns_definitions(self) -> None:
        invoker = _make_invoker("echo", "search")
        defs = get_tool_definitions(
            invoker,
            loaded_tools=frozenset({"echo", "search"}),
        )
        assert defs is not None
        assert len(defs) == 2


# ── response_to_message ─────────────────────────────────────────────


@pytest.mark.unit
class TestResponseToMessage:
    def test_basic_message(self) -> None:
        response = _stop_response("Hello")
        msg = response_to_message(response)
        assert msg.role == MessageRole.ASSISTANT
        assert msg.content == "Hello"
        assert msg.tool_calls == ()

    def test_with_tool_calls(self) -> None:
        response = _tool_use_response("echo")
        msg = response_to_message(response)
        assert msg.role == MessageRole.ASSISTANT
        assert len(msg.tool_calls) == 1


# ── make_turn_record ────────────────────────────────────────────────


@pytest.mark.unit
class TestMakeTurnRecord:
    def test_basic(self) -> None:
        response = _stop_response()
        record = make_turn_record(1, response)
        assert record.turn_number == 1
        assert record.input_tokens == 10
        assert record.output_tokens == 5
        assert record.cost == 0.001
        assert record.tool_calls_made == ()
        assert record.finish_reason == FinishReason.STOP
        assert record.call_category is None

    def test_with_tool_calls(self) -> None:
        response = _tool_use_response("echo")
        record = make_turn_record(2, response)
        assert record.tool_calls_made == ("echo",)
        assert record.finish_reason == FinishReason.TOOL_USE
        assert len(record.tool_call_fingerprints) == 1
        assert record.tool_call_fingerprints[0].startswith("echo:")

    def test_no_tool_calls_empty_fingerprints(self) -> None:
        response = _stop_response()
        record = make_turn_record(1, response)
        assert record.tool_call_fingerprints == ()

    def test_with_call_category(self) -> None:
        response = _stop_response()
        record = make_turn_record(
            1,
            response,
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        assert record.call_category == LLMCallCategory.PRODUCTIVE

    def test_system_category(self) -> None:
        response = _stop_response()
        record = make_turn_record(
            1,
            response,
            call_category=LLMCallCategory.SYSTEM,
        )
        assert record.call_category == LLMCallCategory.SYSTEM

    def test_provider_metadata_latency_extracted(self) -> None:
        """latency_ms extracted from _synthorg_latency_ms key."""
        response = _stop_response()
        response = response.model_copy(
            update={"provider_metadata": {"_synthorg_latency_ms": 123.5}},
        )
        record = make_turn_record(
            1, response, provider_metadata=response.provider_metadata
        )
        assert record.latency_ms == 123.5

    def test_provider_metadata_retry_count_extracted(self) -> None:
        """retry_count extracted from _synthorg_retry_count key."""
        response = _stop_response()
        response = response.model_copy(
            update={"provider_metadata": {"_synthorg_retry_count": 2}},
        )
        record = make_turn_record(
            1, response, provider_metadata=response.provider_metadata
        )
        assert record.retry_count == 2

    def test_provider_metadata_retry_reason_extracted(self) -> None:
        """retry_reason extracted from _synthorg_retry_reason key."""
        response = _stop_response()
        response = response.model_copy(
            update={
                "provider_metadata": {
                    "_synthorg_retry_reason": "RateLimitError",
                    "_synthorg_retry_count": 1,
                },
            },
        )
        record = make_turn_record(
            1, response, provider_metadata=response.provider_metadata
        )
        assert record.retry_reason == "RateLimitError"
        assert record.retry_count == 1

    def test_provider_metadata_cache_hit_extracted(self) -> None:
        """cache_hit extracted from _synthorg_cache_hit key."""
        response = _stop_response()
        response = response.model_copy(
            update={"provider_metadata": {"_synthorg_cache_hit": True}},
        )
        record = make_turn_record(
            1, response, provider_metadata=response.provider_metadata
        )
        assert record.cache_hit is True

    def test_no_provider_metadata_all_none(self) -> None:
        """Without provider_metadata all new fields default to None."""
        record = make_turn_record(1, _stop_response())
        assert record.latency_ms is None
        assert record.cache_hit is None
        assert record.retry_count is None
        assert record.retry_reason is None

    def test_empty_provider_metadata_all_none(self) -> None:
        """Empty provider_metadata dict leaves all new fields as None."""
        record = make_turn_record(1, _stop_response(), provider_metadata={})
        assert record.latency_ms is None
        assert record.retry_count is None

    def test_success_computed_stop(self) -> None:
        """success=True for STOP finish_reason."""
        record = make_turn_record(1, _stop_response())
        assert record.success is True

    def test_success_computed_error(self) -> None:
        """success=False for ERROR finish_reason."""
        response = CompletionResponse(
            content=None,
            finish_reason=FinishReason.ERROR,
            usage=_usage(),
            model="test-model-001",
        )
        record = make_turn_record(1, response)
        assert record.success is False


# ── build_result ────────────────────────────────────────────────────


@pytest.mark.unit
class TestBuildResult:
    def test_basic(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = build_result(
            sample_agent_context,
            TerminationReason.COMPLETED,
            [],
        )
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.turns == ()
        assert result.error_message is None
        assert result.metadata == {}

    def test_with_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = build_result(
            sample_agent_context,
            TerminationReason.ERROR,
            [],
            error_message="something broke",
        )
        assert result.error_message == "something broke"

    def test_with_metadata(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = build_result(
            sample_agent_context,
            TerminationReason.COMPLETED,
            [],
            metadata={"plan": "steps"},
        )
        assert result.metadata == {"plan": "steps"}

    def test_with_turns(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        turns = [
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
                finish_reason=FinishReason.STOP,
            ),
        ]
        result = build_result(
            sample_agent_context,
            TerminationReason.COMPLETED,
            turns,
        )
        assert len(result.turns) == 1


# ── clear_last_turn_tool_calls ─────────────────────────────────────


@pytest.mark.unit
class TestClearLastTurnToolCalls:
    def test_clears_tool_calls_on_last_turn(self) -> None:
        turns = [
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
                tool_calls_made=("search", "read"),
                tool_call_fingerprints=("read:abc123", "search:def456"),
                finish_reason=FinishReason.TOOL_USE,
            ),
        ]
        clear_last_turn_tool_calls(turns)
        assert turns[-1].tool_calls_made == ()
        assert turns[-1].tool_call_fingerprints == ()
        # Other fields unchanged
        assert turns[-1].turn_number == 1
        assert turns[-1].finish_reason == FinishReason.TOOL_USE

    def test_empty_turns_is_noop(self) -> None:
        turns: list[TurnRecord] = []
        clear_last_turn_tool_calls(turns)
        assert turns == []

    def test_preserves_earlier_turns(self) -> None:
        turns = [
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
                tool_calls_made=("search",),
                finish_reason=FinishReason.TOOL_USE,
            ),
            TurnRecord(
                turn_number=2,
                input_tokens=20,
                output_tokens=10,
                cost=0.002,
                tool_calls_made=("write",),
                finish_reason=FinishReason.TOOL_USE,
            ),
        ]
        clear_last_turn_tool_calls(turns)
        # First turn unchanged
        assert turns[0].tool_calls_made == ("search",)
        # Last turn cleared
        assert turns[1].tool_calls_made == ()


# ---------------------------------------------------------------------------
# check_stagnation
# ---------------------------------------------------------------------------


def _stagnation_turn(
    turn_number: int,
    fingerprints: tuple[str, ...] = (),
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=10,
        output_tokens=5,
        cost=0.001,
        tool_call_fingerprints=fingerprints,
        finish_reason=FinishReason.TOOL_USE if fingerprints else FinishReason.STOP,
    )


class _FakeDetector:
    """Minimal fake StagnationDetector for check_stagnation tests."""

    def __init__(self, result: object) -> None:
        from synthorg.engine.stagnation.models import NO_STAGNATION_RESULT

        self._result = result
        self._default = NO_STAGNATION_RESULT

    def get_detector_type(self) -> str:
        return "fake"

    async def check(
        self,
        turns: tuple[TurnRecord, ...],
        *,
        corrections_injected: int = 0,
    ) -> object:
        return self._result


class _RaisingDetector:
    """Detector that raises a configurable exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def get_detector_type(self) -> str:
        return "raising"

    async def check(
        self,
        turns: tuple[TurnRecord, ...],
        *,
        corrections_injected: int = 0,
    ) -> object:
        raise self._exc


@pytest.mark.unit
class TestCheckStagnation:
    """Direct unit tests for check_stagnation()."""

    async def test_none_detector_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = await check_stagnation(
            sample_agent_context,
            None,
            [],
            0,
        )
        assert result is None

    async def test_no_stagnation_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        from synthorg.engine.stagnation.models import NO_STAGNATION_RESULT

        detector = _FakeDetector(NO_STAGNATION_RESULT)
        result = await check_stagnation(
            sample_agent_context,
            detector,  # type: ignore[arg-type]
            [_stagnation_turn(1, ("a:1234567890123456",))],
            0,
        )
        assert result is None

    async def test_terminate_returns_execution_result(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        from synthorg.engine.loop_protocol import ExecutionResult
        from synthorg.engine.stagnation.models import (
            StagnationResult,
            StagnationVerdict,
        )

        terminate = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            repetition_ratio=0.9,
        )
        detector = _FakeDetector(terminate)
        result = await check_stagnation(
            sample_agent_context,
            detector,  # type: ignore[arg-type]
            [_stagnation_turn(1, ("a:1234567890123456",))],
            0,
        )
        assert isinstance(result, ExecutionResult)
        assert result.termination_reason == TerminationReason.STAGNATION
        assert "stagnation" in result.metadata

    async def test_terminate_with_step_number(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        from synthorg.engine.loop_protocol import ExecutionResult
        from synthorg.engine.stagnation.models import (
            StagnationResult,
            StagnationVerdict,
        )

        terminate = StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            repetition_ratio=0.9,
        )
        detector = _FakeDetector(terminate)
        result = await check_stagnation(
            sample_agent_context,
            detector,  # type: ignore[arg-type]
            [_stagnation_turn(1, ("a:1234567890123456",))],
            0,
            step_number=3,
        )
        assert isinstance(result, ExecutionResult)
        assert result.metadata["step_number"] == 3

    async def test_inject_prompt_returns_ctx_and_counter(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        from synthorg.engine.stagnation.models import (
            StagnationResult,
            StagnationVerdict,
        )

        inject = StagnationResult(
            verdict=StagnationVerdict.INJECT_PROMPT,
            corrective_message="Try again.",
            repetition_ratio=0.7,
        )
        detector = _FakeDetector(inject)
        result = await check_stagnation(
            sample_agent_context,
            detector,  # type: ignore[arg-type]
            [_stagnation_turn(1, ("a:1234567890123456",))],
            2,
        )
        assert isinstance(result, tuple)
        ctx, corrections = result
        assert corrections == 3
        user_msgs = [m for m in ctx.conversation if m.role == MessageRole.USER]
        assert any("Try again." in (m.content or "") for m in user_msgs)

    async def test_memory_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        detector = _RaisingDetector(MemoryError("oom"))
        with pytest.raises(MemoryError, match="oom"):
            await check_stagnation(
                sample_agent_context,
                detector,  # type: ignore[arg-type]
                [],
                0,
            )

    async def test_recursion_error_propagates(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        detector = _RaisingDetector(RecursionError("deep"))
        with pytest.raises(RecursionError, match="deep"):
            await check_stagnation(
                sample_agent_context,
                detector,  # type: ignore[arg-type]
                [],
                0,
            )

    async def test_generic_exception_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        detector = _RaisingDetector(ValueError("bad value"))
        result = await check_stagnation(
            sample_agent_context,
            detector,  # type: ignore[arg-type]
            [],
            0,
        )
        assert result is None


@pytest.mark.unit
class TestInvokeCompaction:
    """Direct unit tests for invoke_compaction()."""

    async def test_none_callback_returns_none(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = await invoke_compaction(sample_agent_context, None, 1)
        assert result is None

    async def test_returns_compacted_context(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        async def _callback(ctx: AgentContext) -> AgentContext:
            return ctx

        result = await invoke_compaction(sample_agent_context, _callback, 1)
        assert result is sample_agent_context

    async def test_swallows_callback_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        async def _callback(ctx: AgentContext) -> AgentContext:
            msg = "compaction boom"
            raise ValueError(msg)

        result = await invoke_compaction(sample_agent_context, _callback, 1)
        assert result is None

    async def test_reraises_critical_error(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        async def _callback(ctx: AgentContext) -> AgentContext:
            raise MemoryError

        with pytest.raises(MemoryError):
            await invoke_compaction(sample_agent_context, _callback, 1)
