"""Tests for provider-layer domain models."""

import pytest
from pydantic import ValidationError

from ai_company.providers.enums import FinishReason, MessageRole, StreamEventType
from ai_company.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

from .conftest import (
    ChatMessageFactory,
    CompletionConfigFactory,
    CompletionResponseFactory,
    StreamChunkFactory,
    TokenUsageFactory,
    ToolCallFactory,
    ToolDefinitionFactory,
    ToolResultFactory,
)

pytestmark = pytest.mark.timeout(30)


# ── TokenUsage ────────────────────────────────────────────────────


@pytest.mark.unit
class TestTokenUsage:
    """Tests for TokenUsage validation and immutability."""

    def test_valid(self, sample_token_usage: TokenUsage) -> None:
        assert sample_token_usage.input_tokens == 4500
        assert sample_token_usage.output_tokens == 1200
        assert sample_token_usage.total_tokens == 5700
        assert sample_token_usage.cost_usd == 0.0315

    def test_negative_input_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenUsage(
                input_tokens=-1,
                output_tokens=0,
                cost_usd=0.0,
            )

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenUsage(
                input_tokens=100,
                output_tokens=0,
                cost_usd=-0.01,
            )

    def test_zero_tokens_valid(self) -> None:
        usage = TokenUsage(
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )
        assert usage.total_tokens == 0

    def test_frozen(self, sample_token_usage: TokenUsage) -> None:
        with pytest.raises(ValidationError):
            sample_token_usage.cost_usd = 999.0  # type: ignore[misc]

    def test_total_tokens_is_always_computed(self) -> None:
        usage = TokenUsage(input_tokens=10, output_tokens=5, cost_usd=0.0)
        assert usage.total_tokens == 15

    def test_total_tokens_in_serialization(self) -> None:
        usage = TokenUsage(input_tokens=100, output_tokens=50, cost_usd=0.01)
        dumped = usage.model_dump()
        assert dumped["total_tokens"] == 150

    def test_total_tokens_roundtrip(self) -> None:
        """Stale total_tokens in serialized data is ignored on load."""
        payload = TokenUsage(
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.0,
        ).model_dump()
        payload["total_tokens"] = 999
        usage = TokenUsage.model_validate(payload)
        assert usage.total_tokens == 15

        # JSON roundtrip also recomputes correctly
        json_str = TokenUsage(
            input_tokens=20,
            output_tokens=10,
            cost_usd=0.01,
        ).model_dump_json()
        restored = TokenUsage.model_validate_json(json_str)
        assert restored.total_tokens == 30

    def test_total_tokens_not_assignable(self) -> None:
        """Computed property rejects direct assignment."""
        usage = TokenUsage(input_tokens=10, output_tokens=5, cost_usd=0.0)
        with pytest.raises((ValidationError, AttributeError)):
            usage.total_tokens = 999  # type: ignore[misc]

    def test_factory(self) -> None:
        usage = TokenUsageFactory.build()
        assert isinstance(usage, TokenUsage)
        assert usage.total_tokens == usage.input_tokens + usage.output_tokens


# ── ToolDefinition ────────────────────────────────────────────────


@pytest.mark.unit
class TestToolDefinition:
    """Tests for ToolDefinition validation."""

    def test_valid(self, sample_tool_definition: ToolDefinition) -> None:
        assert sample_tool_definition.name == "search_code"
        assert "query" in sample_tool_definition.parameters_schema["properties"]

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolDefinition(name="", description="x")

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            ToolDefinition(name="  ", description="x")

    def test_empty_schema_default(self) -> None:
        tool = ToolDefinition(name="ping")
        assert tool.parameters_schema == {}

    def test_factory(self) -> None:
        tool = ToolDefinitionFactory.build()
        assert isinstance(tool, ToolDefinition)


# ── ToolCall ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestToolCall:
    """Tests for ToolCall validation."""

    def test_valid(self, sample_tool_call: ToolCall) -> None:
        assert sample_tool_call.id == "call_abc123"
        assert sample_tool_call.name == "search_code"
        assert sample_tool_call.arguments["query"] == "def main"

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolCall(id="", name="test", arguments={})

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolCall(id="call_1", name="", arguments={})

    def test_default_arguments(self) -> None:
        call = ToolCall(id="call_1", name="ping")
        assert call.arguments == {}

    def test_factory(self) -> None:
        call = ToolCallFactory.build()
        assert isinstance(call, ToolCall)


# ── ToolResult ────────────────────────────────────────────────────


@pytest.mark.unit
class TestToolResult:
    """Tests for ToolResult validation."""

    def test_valid(self, sample_tool_result: ToolResult) -> None:
        assert sample_tool_result.tool_call_id == "call_abc123"
        assert sample_tool_result.is_error is False

    def test_empty_tool_call_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolResult(tool_call_id="", content="ok")

    def test_error_result(self) -> None:
        result = ToolResult(
            tool_call_id="call_1",
            content="file not found",
            is_error=True,
        )
        assert result.is_error is True

    def test_factory(self) -> None:
        result = ToolResultFactory.build()
        assert isinstance(result, ToolResult)


# ── ChatMessage ───────────────────────────────────────────────────


@pytest.mark.unit
class TestChatMessage:
    """Tests for ChatMessage role-based validation."""

    def test_valid_user_message(self, sample_user_message: ChatMessage) -> None:
        assert sample_user_message.role == MessageRole.USER
        assert sample_user_message.content == "Hello!"

    def test_valid_assistant_message(
        self,
        sample_assistant_message: ChatMessage,
    ) -> None:
        assert sample_assistant_message.role == MessageRole.ASSISTANT

    def test_valid_system_message(self) -> None:
        msg = ChatMessage(role=MessageRole.SYSTEM, content="You are helpful.")
        assert msg.role == MessageRole.SYSTEM

    def test_valid_tool_message(self) -> None:
        msg = ChatMessage(
            role=MessageRole.TOOL,
            content=None,
            tool_result=ToolResult(
                tool_call_id="call_1",
                content="42",
            ),
        )
        assert msg.role == MessageRole.TOOL

    def test_valid_assistant_with_tool_calls(self) -> None:
        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=(ToolCall(id="call_1", name="ping", arguments={}),),
        )
        assert len(msg.tool_calls) == 1

    def test_valid_assistant_with_content_and_tool_calls(self) -> None:
        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content="I'll search for that.",
            tool_calls=(ToolCall(id="c1", name="search", arguments={}),),
        )
        assert msg.content == "I'll search for that."
        assert len(msg.tool_calls) == 1

    def test_tool_message_requires_tool_result(self) -> None:
        with pytest.raises(ValidationError, match="tool_result"):
            ChatMessage(role=MessageRole.TOOL, content="hi")

    def test_tool_message_rejects_tool_calls(self) -> None:
        with pytest.raises(ValidationError, match="tool_calls"):
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=ToolResult(tool_call_id="c1", content="ok"),
                tool_calls=(ToolCall(id="c1", name="x", arguments={}),),
            )

    def test_assistant_rejects_tool_result(self) -> None:
        with pytest.raises(ValidationError, match="tool_result"):
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="hi",
                tool_result=ToolResult(tool_call_id="c1", content="ok"),
            )

    def test_user_rejects_tool_calls(self) -> None:
        with pytest.raises(ValidationError, match="tool_calls"):
            ChatMessage(
                role=MessageRole.USER,
                content="hi",
                tool_calls=(ToolCall(id="c1", name="x", arguments={}),),
            )

    def test_system_rejects_tool_result(self) -> None:
        with pytest.raises(ValidationError, match="tool_result"):
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="hi",
                tool_result=ToolResult(tool_call_id="c1", content="ok"),
            )

    def test_user_requires_content(self) -> None:
        with pytest.raises(ValidationError, match="content or tool_calls"):
            ChatMessage(role=MessageRole.USER, content=None)

    def test_system_requires_content(self) -> None:
        with pytest.raises(ValidationError, match="content or tool_calls"):
            ChatMessage(role=MessageRole.SYSTEM, content=None)

    def test_assistant_requires_content_or_tool_calls(self) -> None:
        with pytest.raises(ValidationError, match="content or tool_calls"):
            ChatMessage(role=MessageRole.ASSISTANT, content=None)

    def test_system_rejects_tool_calls(self) -> None:
        with pytest.raises(ValidationError, match="tool_calls"):
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are helpful.",
                tool_calls=(ToolCall(id="c1", name="x", arguments={}),),
            )

    def test_user_rejects_tool_result(self) -> None:
        with pytest.raises(ValidationError, match="tool_result"):
            ChatMessage(
                role=MessageRole.USER,
                content="hi",
                tool_result=ToolResult(tool_call_id="c1", content="ok"),
            )

    def test_factory(self) -> None:
        msg = ChatMessageFactory.build()
        assert isinstance(msg, ChatMessage)


# ── CompletionConfig ──────────────────────────────────────────────


@pytest.mark.unit
class TestCompletionConfig:
    """Tests for CompletionConfig validation."""

    def test_all_defaults(self) -> None:
        cfg = CompletionConfig()
        assert cfg.temperature is None
        assert cfg.max_tokens is None
        assert cfg.stop_sequences == ()
        assert cfg.top_p is None
        assert cfg.timeout is None

    def test_temperature_range(self) -> None:
        cfg = CompletionConfig(temperature=0.0)
        assert cfg.temperature == 0.0
        cfg = CompletionConfig(temperature=2.0)
        assert cfg.temperature == 2.0

    def test_temperature_too_high(self) -> None:
        with pytest.raises(ValidationError):
            CompletionConfig(temperature=2.1)

    def test_temperature_negative(self) -> None:
        with pytest.raises(ValidationError):
            CompletionConfig(temperature=-0.1)

    def test_max_tokens_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CompletionConfig(max_tokens=0)

    def test_top_p_range(self) -> None:
        cfg = CompletionConfig(top_p=0.0)
        assert cfg.top_p == 0.0
        cfg = CompletionConfig(top_p=1.0)
        assert cfg.top_p == 1.0

    def test_top_p_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            CompletionConfig(top_p=1.1)

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            CompletionConfig(timeout=0.0)

    def test_factory(self) -> None:
        cfg = CompletionConfigFactory.build()
        assert isinstance(cfg, CompletionConfig)


# ── CompletionResponse ────────────────────────────────────────────


@pytest.mark.unit
class TestCompletionResponse:
    """Tests for CompletionResponse validation."""

    def test_valid(self, sample_completion_response: CompletionResponse) -> None:
        assert sample_completion_response.content == "The answer is 42."
        assert sample_completion_response.finish_reason == FinishReason.STOP

    def test_content_can_be_none(self, sample_token_usage: TokenUsage) -> None:
        resp = CompletionResponse(
            content=None,
            tool_calls=(ToolCall(id="c1", name="ping", arguments={}),),
            finish_reason=FinishReason.TOOL_USE,
            usage=sample_token_usage,
            model="test",
        )
        assert resp.content is None
        assert len(resp.tool_calls) == 1

    def test_empty_model_rejected(self, sample_token_usage: TokenUsage) -> None:
        with pytest.raises(ValidationError):
            CompletionResponse(
                content="hi",
                finish_reason=FinishReason.STOP,
                usage=sample_token_usage,
                model="",
            )

    def test_empty_response_rejected_for_stop(
        self,
        sample_token_usage: TokenUsage,
    ) -> None:
        with pytest.raises(ValidationError, match="must have content"):
            CompletionResponse(
                content=None,
                finish_reason=FinishReason.STOP,
                usage=sample_token_usage,
                model="test",
            )

    def test_empty_response_rejected_for_max_tokens(
        self,
        sample_token_usage: TokenUsage,
    ) -> None:
        with pytest.raises(ValidationError, match="must have content"):
            CompletionResponse(
                content=None,
                finish_reason=FinishReason.MAX_TOKENS,
                usage=sample_token_usage,
                model="test",
            )

    def test_empty_response_rejected_for_tool_use(
        self,
        sample_token_usage: TokenUsage,
    ) -> None:
        with pytest.raises(ValidationError, match="must have content"):
            CompletionResponse(
                content=None,
                finish_reason=FinishReason.TOOL_USE,
                usage=sample_token_usage,
                model="test",
            )

    def test_empty_response_allowed_for_content_filter(
        self,
        sample_token_usage: TokenUsage,
    ) -> None:
        resp = CompletionResponse(
            content=None,
            finish_reason=FinishReason.CONTENT_FILTER,
            usage=sample_token_usage,
            model="test",
        )
        assert resp.content is None

    def test_empty_response_allowed_for_error(
        self,
        sample_token_usage: TokenUsage,
    ) -> None:
        resp = CompletionResponse(
            content=None,
            finish_reason=FinishReason.ERROR,
            usage=sample_token_usage,
            model="test",
        )
        assert resp.finish_reason == FinishReason.ERROR

    def test_frozen(self, sample_completion_response: CompletionResponse) -> None:
        with pytest.raises(ValidationError):
            sample_completion_response.content = "new"  # type: ignore[misc]

    def test_factory(self) -> None:
        resp = CompletionResponseFactory.build()
        assert isinstance(resp, CompletionResponse)


# ── StreamChunk ───────────────────────────────────────────────────


@pytest.mark.unit
class TestStreamChunk:
    """Tests for StreamChunk event-type validation."""

    def test_content_delta_valid(self) -> None:
        chunk = StreamChunk(
            event_type=StreamEventType.CONTENT_DELTA,
            content="Hello",
        )
        assert chunk.content == "Hello"

    def test_content_delta_requires_content(self) -> None:
        with pytest.raises(ValidationError, match=r"content_delta.*content"):
            StreamChunk(event_type=StreamEventType.CONTENT_DELTA)

    def test_tool_call_delta_valid(self) -> None:
        tc = ToolCall(id="c1", name="ping", arguments={})
        chunk = StreamChunk(
            event_type=StreamEventType.TOOL_CALL_DELTA,
            tool_call_delta=tc,
        )
        assert chunk.tool_call_delta is not None

    def test_tool_call_delta_requires_delta(self) -> None:
        with pytest.raises(ValidationError, match="tool_call_delta"):
            StreamChunk(event_type=StreamEventType.TOOL_CALL_DELTA)

    def test_usage_event_valid(self, sample_token_usage: TokenUsage) -> None:
        chunk = StreamChunk(
            event_type=StreamEventType.USAGE,
            usage=sample_token_usage,
        )
        assert chunk.usage is not None

    def test_usage_event_requires_usage(self) -> None:
        with pytest.raises(ValidationError, match="usage"):
            StreamChunk(event_type=StreamEventType.USAGE)

    def test_error_event_valid(self) -> None:
        chunk = StreamChunk(
            event_type=StreamEventType.ERROR,
            error_message="something broke",
        )
        assert chunk.error_message == "something broke"

    def test_error_event_requires_message(self) -> None:
        with pytest.raises(ValidationError, match=r"error.*error_message"):
            StreamChunk(event_type=StreamEventType.ERROR)

    def test_done_event(self) -> None:
        chunk = StreamChunk(event_type=StreamEventType.DONE)
        assert chunk.event_type == StreamEventType.DONE

    def test_done_rejects_extraneous_content(self) -> None:
        with pytest.raises(ValidationError, match="must not include"):
            StreamChunk(
                event_type=StreamEventType.DONE,
                content="extra",
            )

    def test_content_delta_rejects_extraneous_usage(
        self,
        sample_token_usage: TokenUsage,
    ) -> None:
        with pytest.raises(ValidationError, match="must not include"):
            StreamChunk(
                event_type=StreamEventType.CONTENT_DELTA,
                content="Hello",
                usage=sample_token_usage,
            )

    def test_factory(self) -> None:
        chunk = StreamChunkFactory.build()
        assert isinstance(chunk, StreamChunk)

    def test_json_roundtrip(self, sample_token_usage: TokenUsage) -> None:
        chunk = StreamChunk(
            event_type=StreamEventType.USAGE,
            usage=sample_token_usage,
        )
        json_str = chunk.model_dump_json()
        restored = StreamChunk.model_validate_json(json_str)
        assert restored.usage == chunk.usage


# ── __all__ exports ──────────────────────────────────────────────


@pytest.mark.unit
class TestExports:
    """Verify that __all__ matches the importable public API."""

    def test_all_exports_are_importable(self) -> None:
        import ai_company.providers as pkg

        for name in pkg.__all__:
            assert hasattr(pkg, name), f"{name} in __all__ but not importable"
