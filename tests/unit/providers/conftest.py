"""Unit test configuration and fixtures for provider models."""

from collections.abc import AsyncIterator

import pytest
from polyfactory.factories.pydantic_factory import ModelFactory

from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import FinishReason, MessageRole, StreamEventType
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

# ── Factories ──────────────────────────────────────────────────────


class TokenUsageFactory(ModelFactory[TokenUsage]):
    __model__ = TokenUsage
    input_tokens = 100
    output_tokens = 50
    cost_usd = 0.001


class ToolDefinitionFactory(ModelFactory[ToolDefinition]):
    __model__ = ToolDefinition
    name = "get_weather"
    description = "Get current weather for a location"
    parameters_schema = {  # noqa: RUF012
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    }


class ToolCallFactory(ModelFactory[ToolCall]):
    __model__ = ToolCall
    id = "call_001"
    name = "get_weather"
    arguments = {"location": "London"}  # noqa: RUF012


class ToolResultFactory(ModelFactory[ToolResult]):
    __model__ = ToolResult
    tool_call_id = "call_001"
    content = "Sunny, 22°C"
    is_error = False


class ChatMessageFactory(ModelFactory[ChatMessage]):
    __model__ = ChatMessage
    role = MessageRole.USER
    content = "Hello"
    tool_calls = ()
    tool_result = None


class CompletionConfigFactory(ModelFactory[CompletionConfig]):
    __model__ = CompletionConfig
    temperature = 0.7
    max_tokens = 1024
    stop_sequences = ()
    top_p = None
    timeout = None


class CompletionResponseFactory(ModelFactory[CompletionResponse]):
    __model__ = CompletionResponse
    content = "Hello! How can I help you?"
    tool_calls = ()
    finish_reason = FinishReason.STOP
    usage = TokenUsageFactory
    model = "test-model"
    provider_request_id = None


class StreamChunkFactory(ModelFactory[StreamChunk]):
    __model__ = StreamChunk
    event_type = StreamEventType.CONTENT_DELTA
    content = "Hello"
    tool_call_delta = None
    usage = None
    error_message = None


class ModelCapabilitiesFactory(ModelFactory[ModelCapabilities]):
    __model__ = ModelCapabilities
    model_id = "test-model"
    provider = "test-provider"
    max_context_tokens = 200_000
    max_output_tokens = 8_192
    supports_tools = True
    supports_vision = True
    supports_streaming = True
    supports_streaming_tool_calls = True
    supports_system_messages = True
    cost_per_1k_input = 0.003
    cost_per_1k_output = 0.015


# ── FakeProvider for protocol/base tests ──────────────────────────


class FakeProvider:
    """Minimal provider that satisfies ``CompletionProvider`` structurally."""

    def __init__(self, capabilities: ModelCapabilities | None = None) -> None:
        self._capabilities = capabilities or ModelCapabilitiesFactory.build()
        self.complete_calls: list[
            tuple[
                list[ChatMessage],
                str,
                list[ToolDefinition] | None,
                CompletionConfig | None,
            ]
        ] = []
        self.stream_calls: list[
            tuple[
                list[ChatMessage],
                str,
                list[ToolDefinition] | None,
                CompletionConfig | None,
            ]
        ] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.complete_calls.append((messages, model, tools, config))
        return CompletionResponseFactory.build()

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.stream_calls.append((messages, model, tools, config))

        async def _gen() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(
                event_type=StreamEventType.CONTENT_DELTA,
                content="Hi",
            )
            yield StreamChunk(
                event_type=StreamEventType.DONE,
            )

        return _gen()

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        return self._capabilities


# ── Sample Fixtures ───────────────────────────────────────────────


@pytest.fixture
def sample_token_usage() -> TokenUsage:
    return TokenUsage(
        input_tokens=4500,
        output_tokens=1200,
        cost_usd=0.0315,
    )


@pytest.fixture
def sample_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="search_code",
        description="Search the codebase for a pattern",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    )


@pytest.fixture
def sample_tool_call() -> ToolCall:
    return ToolCall(
        id="call_abc123",
        name="search_code",
        arguments={"query": "def main", "max_results": 5},
    )


@pytest.fixture
def sample_tool_result() -> ToolResult:
    return ToolResult(
        tool_call_id="call_abc123",
        content="Found 3 matches",
        is_error=False,
    )


@pytest.fixture
def sample_user_message() -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content="Hello!")


@pytest.fixture
def sample_assistant_message() -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content="Hi there! How can I help?",
    )


@pytest.fixture
def sample_completion_response(sample_token_usage: TokenUsage) -> CompletionResponse:
    return CompletionResponse(
        content="The answer is 42.",
        finish_reason=FinishReason.STOP,
        usage=sample_token_usage,
        model="test-model",
    )


@pytest.fixture
def sample_model_capabilities() -> ModelCapabilities:
    return ModelCapabilities(
        model_id="test-model",
        provider="test-provider",
        max_context_tokens=200_000,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_vision=True,
        supports_streaming=True,
        supports_streaming_tool_calls=True,
        supports_system_messages=True,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
    )


@pytest.fixture
def fake_provider(sample_model_capabilities: ModelCapabilities) -> FakeProvider:
    return FakeProvider(capabilities=sample_model_capabilities)
