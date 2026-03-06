"""Shared fixtures and response builders for provider integration tests.

Integration tests mock at the ``litellm.acompletion`` level (not HTTP)
so that real ``ModelResponse`` attribute access paths are exercised
through ``_map_response``, ``_process_chunk``, and ``extract_tool_calls``.
"""

from typing import TYPE_CHECKING, Any

import pytest
from litellm import ModelResponse
from litellm.types.llms.openai import ChatCompletionToolCallFunctionChunk
from litellm.types.utils import (  # type: ignore[attr-defined]
    ChatCompletionToolCallChunk,
    Delta,
    StreamingChoices,
    Usage,
)

from ai_company.config.schema import ProviderConfig, ProviderModelConfig, RetryConfig
from ai_company.providers.enums import MessageRole
from ai_company.providers.models import (
    ChatMessage,
    ToolDefinition,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# ── Config factories ──────────────────────────────────────────────


def make_provider_config() -> dict[str, ProviderConfig]:
    """Provider config with two fake models."""
    return {
        "example-provider": ProviderConfig(
            driver="litellm",
            api_key="sk-test-key",
            models=(
                ProviderModelConfig(
                    id="test-model-001",
                    alias="medium",
                    cost_per_1k_input=0.003,
                    cost_per_1k_output=0.015,
                    max_context=200_000,
                ),
                ProviderModelConfig(
                    id="test-model-002",
                    alias="small",
                    cost_per_1k_input=0.001,
                    cost_per_1k_output=0.005,
                    max_context=200_000,
                ),
            ),
            retry=RetryConfig(max_retries=0),
        ),
    }


def make_openrouter_config() -> dict[str, ProviderConfig]:
    """Provider config with custom base_url and two fake models (OpenRouter-shaped)."""
    return {
        "openrouter": ProviderConfig(
            driver="litellm",
            api_key="sk-or-test-key",
            base_url="https://openrouter.ai/api/v1",
            models=(
                ProviderModelConfig(
                    id="test-model-openrouter-001",
                    alias="or-medium",
                    cost_per_1k_input=0.003,
                    cost_per_1k_output=0.015,
                    max_context=200_000,
                ),
                ProviderModelConfig(
                    id="test-model-openrouter-002",
                    alias="llama-70b",
                    cost_per_1k_input=0.0008,
                    cost_per_1k_output=0.0008,
                    max_context=128_000,
                ),
            ),
            retry=RetryConfig(max_retries=0),
        ),
    }


def make_ollama_config() -> dict[str, ProviderConfig]:
    """Provider config — local, no api_key, zero cost (Ollama-shaped)."""
    return {
        "ollama": ProviderConfig(
            driver="litellm",
            api_key=None,
            base_url="http://localhost:11434",
            models=(
                ProviderModelConfig(
                    id="test-model-003",
                    alias="llama",
                    cost_per_1k_input=0.0,
                    cost_per_1k_output=0.0,
                    max_context=128_000,
                ),
            ),
            retry=RetryConfig(max_retries=0),
        ),
    }


# ── Response builders (real litellm objects) ──────────────────────


def build_model_response(  # noqa: PLR0913
    *,
    content: str | None = "Hello! How can I help?",
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    request_id: str = "req_abc123",
    model: str = "test-model-001",
) -> ModelResponse:
    """Build a real ``litellm.ModelResponse`` for non-streaming tests."""
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        **({} if tool_calls is None else {"tool_calls": tool_calls}),
    }
    return ModelResponse(
        id=request_id,
        choices=[
            {
                "message": message,
                "finish_reason": finish_reason,
                "index": 0,
            },
        ],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        model=model,
    )


def build_tool_call_dict(
    *,
    call_id: str = "call_001",
    name: str = "get_weather",
    arguments: str = '{"location": "London"}',
) -> dict[str, Any]:
    """Build a single tool call dict in chat-completion format."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


# ── Streaming helpers ─────────────────────────────────────────────


def build_content_chunk(
    content: str,
    *,
    model: str = "test-model-001",
    chunk_id: str = "chunk_0",
) -> ModelResponse:
    """Build a streaming chunk with text content."""
    return ModelResponse(
        id=chunk_id,
        choices=[
            StreamingChoices(
                delta=Delta(content=content),
                index=0,
                finish_reason=None,
            ),
        ],
        model=model,
        stream=True,
    )


def build_usage_chunk(
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    model: str = "test-model-001",
    chunk_id: str = "chunk_usage",
) -> ModelResponse:
    """Build a streaming chunk with usage data and no choices."""
    return ModelResponse(
        id=chunk_id,
        choices=[],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        model=model,
        stream=True,
    )


def build_tool_call_delta_chunk(  # noqa: PLR0913
    *,
    index: int = 0,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
    model: str = "test-model-001",
    chunk_id: str = "chunk_tc",
) -> ModelResponse:
    """Build a streaming chunk with a tool call delta."""
    tc_delta = ChatCompletionToolCallChunk(
        index=index,
        id=call_id,
        function=ChatCompletionToolCallFunctionChunk(
            name=name, arguments=arguments or ""
        ),
        type="function",
    )
    return ModelResponse(
        id=chunk_id,
        choices=[
            StreamingChoices(
                delta=Delta(content=None, tool_calls=[tc_delta]),
                index=0,
                finish_reason=None,
            ),
        ],
        model=model,
        stream=True,
    )


def build_finish_chunk(
    finish_reason: str = "stop",
    *,
    model: str = "test-model-001",
    chunk_id: str = "chunk_fin",
) -> ModelResponse:
    """Build a streaming chunk with only a finish reason."""
    return ModelResponse(
        id=chunk_id,
        choices=[
            StreamingChoices(
                delta=Delta(content=None),
                index=0,
                finish_reason=finish_reason,
            ),
        ],
        model=model,
        stream=True,
    )


async def async_iter_chunks(
    chunks: list[ModelResponse],
) -> AsyncIterator[ModelResponse]:
    """Wrap a list of ``ModelResponse`` chunks into an ``AsyncIterator``."""
    for chunk in chunks:
        yield chunk


# ── Standard message fixtures ─────────────────────────────────────


@pytest.fixture
def user_messages() -> list[ChatMessage]:
    """Simple single-user-message conversation."""
    return [ChatMessage(role=MessageRole.USER, content="Hello")]


@pytest.fixture
def multi_turn_messages() -> list[ChatMessage]:
    """Multi-turn conversation with system, user, and assistant."""
    return [
        ChatMessage(role=MessageRole.SYSTEM, content="You are helpful."),
        ChatMessage(role=MessageRole.USER, content="What is 2+2?"),
        ChatMessage(role=MessageRole.ASSISTANT, content="4"),
        ChatMessage(role=MessageRole.USER, content="Thanks!"),
    ]


@pytest.fixture
def sample_tool_definitions() -> list[ToolDefinition]:
    """Sample tool definitions for tool-calling tests."""
    return [
        ToolDefinition(
            name="get_weather",
            description="Get weather for a location",
            parameters_schema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                },
                "required": ["location"],
            },
        ),
        ToolDefinition(
            name="search_web",
            description="Search the web",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        ),
    ]
