"""Integration tests: provider end-to-end pipeline.

Exercises the full path from ``ProviderConfig`` through
``ProviderRegistry.from_config()`` to ``driver.complete()`` and
``driver.stream()``, using real ``litellm.ModelResponse`` objects.
"""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.providers.enums import FinishReason, StreamEventType
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
)
from synthorg.providers.registry import ProviderRegistry

from .conftest import (
    async_iter_chunks,
    build_content_chunk,
    build_finish_chunk,
    build_model_response,
    build_usage_chunk,
    make_provider_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]

_PATCH_TARGET = "synthorg.providers.drivers.litellm_driver._litellm.acompletion"


# ── Happy-path: config → registry → complete ─────────────────────


async def test_config_to_registry_to_complete(
    user_messages: list[ChatMessage],
) -> None:
    """Full pipeline: config -> ProviderRegistry -> driver.complete()."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    mock_resp = build_model_response(content="Hi there!")
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(user_messages, "medium")

    assert result.content == "Hi there!"
    assert result.finish_reason == FinishReason.STOP
    assert result.model == "test-model-001"
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50


async def test_alias_resolution(
    user_messages: list[ChatMessage],
) -> None:
    """Model alias 'medium' resolves to full model ID."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "medium")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == "example-provider/test-model-001"


async def test_full_model_id_works(
    user_messages: list[ChatMessage],
) -> None:
    """Full model ID also works without alias."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "test-model-001")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == "example-provider/test-model-001"


async def test_completion_config_forwarded(
    user_messages: list[ChatMessage],
) -> None:
    """CompletionConfig params are forwarded to litellm."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    comp_config = CompletionConfig(
        temperature=0.7,
        max_tokens=1024,
        timeout=30.0,
        top_p=0.9,
        stop_sequences=("STOP", "END"),
    )
    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "medium", config=comp_config)

    kwargs = mock_call.call_args.kwargs
    assert kwargs["temperature"] == 0.7
    assert kwargs["max_tokens"] == 1024
    assert kwargs["timeout"] == 30.0
    assert kwargs["top_p"] == 0.9
    assert kwargs["stop"] == ["STOP", "END"]


async def test_api_key_forwarded(
    user_messages: list[ChatMessage],
) -> None:
    """API key from config is forwarded to litellm."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "medium")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["api_key"] == "sk-test-key"


async def test_cost_computation(
    user_messages: list[ChatMessage],
) -> None:
    """Cost is computed from config rates: medium @ $0.003/$0.015 per 1k."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    mock_resp = build_model_response(prompt_tokens=1000, completion_tokens=500)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(user_messages, "medium")

    # (1000/1000)*0.003 + (500/1000)*0.015 = 0.003 + 0.0075 = 0.0105
    assert result.usage.cost_usd == pytest.approx(0.0105)


async def test_finish_reason_max_tokens(
    user_messages: list[ChatMessage],
) -> None:
    """Finish reason 'length' maps to MAX_TOKENS."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    mock_resp = build_model_response(finish_reason="length")
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(user_messages, "medium")

    assert result.finish_reason == FinishReason.MAX_TOKENS


async def test_small_model(
    user_messages: list[ChatMessage],
) -> None:
    """Second model (small) resolves and computes cost correctly."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    mock_resp = build_model_response(
        model="test-model-002",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        result = await driver.complete(user_messages, "small")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == "example-provider/test-model-002"
    # (1000/1000)*0.001 + (1000/1000)*0.005 = 0.001 + 0.005 = 0.006
    assert result.usage.cost_usd == pytest.approx(0.006)


# ── Streaming pipeline ────────────────────────────────────────────


async def test_stream_basic_text(
    user_messages: list[ChatMessage],
) -> None:
    """Streaming produces CONTENT_DELTA + DONE chunks."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    chunks = [
        build_content_chunk("Hello"),
        build_content_chunk(" world"),
        build_finish_chunk("stop"),
    ]
    mock_stream = async_iter_chunks(chunks)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_stream):
        stream = await driver.stream(user_messages, "medium")
        result = [sc async for sc in stream]

    content_chunks = [
        c for c in result if c.event_type == StreamEventType.CONTENT_DELTA
    ]
    assert len(content_chunks) == 2
    assert content_chunks[0].content == "Hello"
    assert content_chunks[1].content == " world"
    assert result[-1].event_type == StreamEventType.DONE


async def test_stream_usage_chunk(
    user_messages: list[ChatMessage],
) -> None:
    """Streaming usage chunk reports token counts and cost."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    chunks = [
        build_content_chunk("Hi"),
        build_usage_chunk(prompt_tokens=200, completion_tokens=100),
        build_finish_chunk("stop"),
    ]
    mock_stream = async_iter_chunks(chunks)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_stream):
        stream = await driver.stream(user_messages, "medium")
        result = [sc async for sc in stream]

    usage_chunks = [c for c in result if c.event_type == StreamEventType.USAGE]
    assert len(usage_chunks) == 1
    assert usage_chunks[0].usage is not None
    assert usage_chunks[0].usage.input_tokens == 200
    assert usage_chunks[0].usage.output_tokens == 100
    # (200/1000)*0.003 + (100/1000)*0.015 = 0.0006 + 0.0015 = 0.0021
    assert usage_chunks[0].usage.cost_usd == pytest.approx(0.0021)


async def test_stream_multiple_content_deltas(
    user_messages: list[ChatMessage],
) -> None:
    """Multiple content deltas are yielded individually."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    chunks = [
        build_content_chunk("A"),
        build_content_chunk("B"),
        build_content_chunk("C"),
        build_finish_chunk("stop"),
    ]
    mock_stream = async_iter_chunks(chunks)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_stream):
        stream = await driver.stream(user_messages, "medium")
        result = [sc async for sc in stream]

    content_chunks = [
        c for c in result if c.event_type == StreamEventType.CONTENT_DELTA
    ]
    assert len(content_chunks) == 3
    assert "".join(c.content for c in content_chunks if c.content) == "ABC"


async def test_stream_ends_with_done(
    user_messages: list[ChatMessage],
) -> None:
    """Stream always ends with a DONE event."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    chunks = [build_content_chunk("x"), build_finish_chunk("stop")]
    mock_stream = async_iter_chunks(chunks)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_stream):
        stream = await driver.stream(user_messages, "medium")
        result = [sc async for sc in stream]

    assert result[-1].event_type == StreamEventType.DONE


async def test_multi_turn_messages_forwarded(
    multi_turn_messages: list[ChatMessage],
) -> None:
    """Multi-turn messages are forwarded to litellm."""
    config = make_provider_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("example-provider")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(multi_turn_messages, "medium")

    kwargs = mock_call.call_args.kwargs
    messages = kwargs["messages"]
    assert len(messages) == 4
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "user"
