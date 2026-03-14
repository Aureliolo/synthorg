"""Integration tests: tool calling end-to-end pipeline.

Exercises tool definition forwarding, tool call extraction from real
``ModelResponse`` objects, streaming tool call accumulation, and
multi-turn tool conversations.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.providers.enums import FinishReason, MessageRole, StreamEventType
from synthorg.providers.models import (
    ChatMessage,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.providers.base import BaseCompletionProvider

from .conftest import (
    async_iter_chunks,
    build_content_chunk,
    build_finish_chunk,
    build_model_response,
    build_tool_call_delta_chunk,
    build_tool_call_dict,
    make_provider_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]

_PATCH_TARGET = "synthorg.providers.drivers.litellm_driver._litellm.acompletion"


def _make_driver() -> BaseCompletionProvider:
    """Build a provider driver from config."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    return registry.get("example-provider")


# ── Non-streaming tool calls ──────────────────────────────────────


async def test_single_tool_call(
    user_messages: list[ChatMessage],
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """Single tool call is extracted correctly."""
    driver = _make_driver()
    tc = build_tool_call_dict(
        call_id="call_w1",
        name="get_weather",
        arguments='{"location": "London"}',
    )
    mock_resp = build_model_response(
        content=None,
        tool_calls=[tc],
        finish_reason="tool_calls",
    )
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(
            user_messages, "medium", tools=sample_tool_definitions
        )

    assert result.finish_reason == FinishReason.TOOL_USE
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments == {"location": "London"}
    assert result.tool_calls[0].id == "call_w1"
    assert result.content is None


async def test_multiple_tool_calls(
    user_messages: list[ChatMessage],
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """Multiple tool calls in a single response."""
    driver = _make_driver()
    tc1 = build_tool_call_dict(
        call_id="call_w1",
        name="get_weather",
        arguments='{"location": "London"}',
    )
    tc2 = build_tool_call_dict(
        call_id="call_s1",
        name="search_web",
        arguments='{"query": "London weather"}',
    )
    mock_resp = build_model_response(
        content=None,
        tool_calls=[tc1, tc2],
        finish_reason="tool_calls",
    )
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(
            user_messages, "medium", tools=sample_tool_definitions
        )

    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[1].name == "search_web"


async def test_tool_definitions_forwarded(
    user_messages: list[ChatMessage],
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """ToolDefinitions are converted and forwarded to litellm."""
    driver = _make_driver()
    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "medium", tools=sample_tool_definitions)

    kwargs = mock_call.call_args.kwargs
    tools = kwargs["tools"]
    assert len(tools) == 2
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "get_weather"
    assert tools[1]["function"]["name"] == "search_web"


async def test_tool_use_finish_reason(
    user_messages: list[ChatMessage],
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """Finish reason 'tool_calls' maps to TOOL_USE."""
    driver = _make_driver()
    tc = build_tool_call_dict()
    mock_resp = build_model_response(
        content=None,
        tool_calls=[tc],
        finish_reason="tool_calls",
    )
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(
            user_messages, "medium", tools=sample_tool_definitions
        )

    assert result.finish_reason == FinishReason.TOOL_USE


# ── Streaming tool calls ─────────────────────────────────────────


async def test_streaming_single_tool_call(
    user_messages: list[ChatMessage],
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """Streaming tool call is accumulated and emitted as TOOL_CALL_DELTA."""
    driver = _make_driver()
    chunks = [
        build_tool_call_delta_chunk(
            index=0, call_id="call_s1", name="get_weather", arguments='{"lo'
        ),
        build_tool_call_delta_chunk(index=0, arguments='cation": "Paris"}'),
        build_finish_chunk("tool_calls"),
    ]
    mock_stream = async_iter_chunks(chunks)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_stream):
        stream = await driver.stream(
            user_messages, "medium", tools=sample_tool_definitions
        )
        result = [sc async for sc in stream]

    tc_chunks = [c for c in result if c.event_type == StreamEventType.TOOL_CALL_DELTA]
    assert len(tc_chunks) == 1
    tc = tc_chunks[0].tool_call_delta
    assert tc is not None
    assert tc.name == "get_weather"
    assert tc.arguments == {"location": "Paris"}
    assert tc.id == "call_s1"
    assert result[-1].event_type == StreamEventType.DONE


async def test_streaming_multiple_concurrent_tool_calls(
    user_messages: list[ChatMessage],
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """Multiple concurrent streaming tool calls on different indices."""
    driver = _make_driver()
    chunks = [
        # First tool call start
        build_tool_call_delta_chunk(
            index=0,
            call_id="call_w1",
            name="get_weather",
            arguments='{"location": ',
        ),
        # Second tool call start (interleaved)
        build_tool_call_delta_chunk(
            index=1,
            call_id="call_s1",
            name="search_web",
            arguments='{"query": ',
        ),
        # Continue first
        build_tool_call_delta_chunk(index=0, arguments='"London"}'),
        # Continue second
        build_tool_call_delta_chunk(index=1, arguments='"weather"}'),
        build_finish_chunk("tool_calls"),
    ]
    mock_stream = async_iter_chunks(chunks)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_stream):
        stream = await driver.stream(
            user_messages, "medium", tools=sample_tool_definitions
        )
        result = [sc async for sc in stream]

    tc_chunks = [c for c in result if c.event_type == StreamEventType.TOOL_CALL_DELTA]
    assert len(tc_chunks) == 2
    names = {c.tool_call_delta.name for c in tc_chunks if c.tool_call_delta}
    assert names == {"get_weather", "search_web"}


async def test_streaming_mixed_text_and_tool_calls(
    user_messages: list[ChatMessage],
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """Stream with text content followed by tool calls."""
    driver = _make_driver()
    chunks = [
        build_content_chunk("I'll check the weather. "),
        build_tool_call_delta_chunk(
            index=0,
            call_id="call_w1",
            name="get_weather",
            arguments='{"location": "Berlin"}',
        ),
        build_finish_chunk("tool_calls"),
    ]
    mock_stream = async_iter_chunks(chunks)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_stream):
        stream = await driver.stream(
            user_messages, "medium", tools=sample_tool_definitions
        )
        result = [sc async for sc in stream]

    content_chunks = [
        c for c in result if c.event_type == StreamEventType.CONTENT_DELTA
    ]
    tc_chunks = [c for c in result if c.event_type == StreamEventType.TOOL_CALL_DELTA]
    assert len(content_chunks) == 1
    assert content_chunks[0].content == "I'll check the weather. "
    assert len(tc_chunks) == 1
    assert tc_chunks[0].tool_call_delta is not None
    assert tc_chunks[0].tool_call_delta.name == "get_weather"


async def test_streaming_malformed_json_tool_call(
    user_messages: list[ChatMessage],
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """Malformed JSON in streamed tool call args causes the tool call to be dropped."""
    driver = _make_driver()
    chunks = [
        build_tool_call_delta_chunk(
            index=0,
            call_id="call_bad",
            name="get_weather",
            arguments="{not valid json",
        ),
        build_finish_chunk("tool_calls"),
    ]
    mock_stream = async_iter_chunks(chunks)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_stream):
        stream = await driver.stream(
            user_messages, "medium", tools=sample_tool_definitions
        )
        result = [sc async for sc in stream]

    tc_chunks = [c for c in result if c.event_type == StreamEventType.TOOL_CALL_DELTA]
    assert len(tc_chunks) == 0


async def test_multi_turn_tool_conversation(
    sample_tool_definitions: list[ToolDefinition],
) -> None:
    """Multi-turn: user -> assistant(tool_call) -> tool_result -> assistant."""
    driver = _make_driver()

    # Turn 1: user asks, model calls tool
    messages_t1 = [
        ChatMessage(role=MessageRole.USER, content="What's the weather?"),
    ]
    tc = build_tool_call_dict(
        call_id="call_w1",
        name="get_weather",
        arguments='{"location": "Tokyo"}',
    )
    mock_resp_t1 = build_model_response(
        content=None,
        tool_calls=[tc],
        finish_reason="tool_calls",
    )
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp_t1):
        result_t1 = await driver.complete(
            messages_t1, "medium", tools=sample_tool_definitions
        )

    assert result_t1.finish_reason == FinishReason.TOOL_USE
    assert len(result_t1.tool_calls) == 1

    # Turn 2: include tool result, model responds with text
    messages_t2 = [
        ChatMessage(role=MessageRole.USER, content="What's the weather?"),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                ToolCall(
                    id="call_w1",
                    name="get_weather",
                    arguments={"location": "Tokyo"},
                ),
            ),
        ),
        ChatMessage(
            role=MessageRole.TOOL,
            tool_result=ToolResult(
                tool_call_id="call_w1",
                content="Sunny, 25°C",
            ),
        ),
        ChatMessage(role=MessageRole.USER, content="Tell me the result"),
    ]
    mock_resp_t2 = build_model_response(
        content="It's sunny and 25°C in Tokyo!",
        finish_reason="stop",
    )
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp_t2
    ) as mock_call_t2:
        result_t2 = await driver.complete(
            messages_t2, "medium", tools=sample_tool_definitions
        )

    assert result_t2.content == "It's sunny and 25°C in Tokyo!"
    assert result_t2.finish_reason == FinishReason.STOP
    assert len(result_t2.tool_calls) == 0

    # Verify forwarded messages include tool-call and tool-result roles
    fwd = mock_call_t2.call_args.kwargs["messages"]
    assert len(fwd) == 4
    assert fwd[0]["role"] == "user"
    assert fwd[1]["role"] == "assistant"
    assert fwd[1]["tool_calls"][0]["id"] == "call_w1"
    assert fwd[2]["role"] == "tool"
    assert fwd[2]["tool_call_id"] == "call_w1"
    assert fwd[3]["role"] == "user"
