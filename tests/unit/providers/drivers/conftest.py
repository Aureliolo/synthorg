"""Shared fixtures and mock factories for driver tests."""

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# ── Sample ProviderConfig ────────────────────────────────────────


def make_provider_config(  # noqa: PLR0913
    *,
    driver: str = "litellm",
    api_key: str | None = "sk-test-key",
    base_url: str | None = None,
    models: tuple[ProviderModelConfig, ...] | None = None,
    retry: RetryConfig | None = None,
    rate_limiter: RateLimiterConfig | None = None,
) -> ProviderConfig:
    """Build a ``ProviderConfig`` for testing.

    Defaults to retries disabled (``max_retries=0``) so driver tests
    exercise exception mapping in isolation.
    """
    if models is None:
        models = (
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
        )
    return ProviderConfig(
        driver=driver,
        api_key=api_key,
        base_url=base_url,
        models=models,
        retry=retry or RetryConfig(max_retries=0),
        rate_limiter=rate_limiter or RateLimiterConfig(),
    )


@pytest.fixture
def sample_provider_config() -> ProviderConfig:
    """Standard two-model provider config."""
    return make_provider_config()


# ── Mock LiteLLM response objects ────────────────────────────────


def make_mock_response(  # noqa: PLR0913
    *,
    content: str | None = "Hello! How can I help?",
    tool_calls: list[Any] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    request_id: str = "req_abc123",
    model: str = "test-model-001",
) -> MagicMock:
    """Build a mock LiteLLM ``ModelResponse``."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    response.id = request_id
    response.model = model

    return response


def make_mock_tool_call(
    *,
    call_id: str = "call_001",
    name: str = "get_weather",
    arguments: str = '{"location": "London"}',
) -> MagicMock:
    """Build a mock tool call object (chat-completion format)."""
    func = MagicMock()
    func.name = name
    func.arguments = arguments

    tc = MagicMock()
    tc.id = call_id
    tc.type = "function"
    tc.function = func

    return tc


# ── Mock streaming chunks ────────────────────────────────────────


def make_stream_chunk(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> MagicMock:
    """Build a mock streaming chunk."""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls

    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason

    chunk = MagicMock()
    chunk.choices = [choice]

    if prompt_tokens is not None:
        usage = MagicMock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens or 0
        usage.total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
        chunk.usage = usage
    else:
        chunk.usage = None

    return chunk


def make_stream_tool_call_delta(
    *,
    index: int = 0,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> MagicMock:
    """Build a mock streaming tool call delta."""
    func = MagicMock()
    func.name = name
    func.arguments = arguments

    delta = MagicMock()
    delta.index = index
    delta.id = call_id
    delta.function = func

    return delta


async def mock_stream_response(
    chunks: list[MagicMock],
) -> AsyncIterator[MagicMock]:
    """Create an async iterator from a list of mock chunks."""
    for chunk in chunks:
        yield chunk
