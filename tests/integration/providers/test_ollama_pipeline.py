"""Integration tests: Ollama provider end-to-end pipeline.

Verifies local (no api_key) provider, localhost base_url forwarding,
and zero-cost model pricing.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.providers.enums import FinishReason
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.providers.models import ChatMessage

from .conftest import build_model_response, make_ollama_config

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]

_PATCH_TARGET = "synthorg.providers.drivers.litellm_driver._litellm.acompletion"


async def test_no_api_key(
    user_messages: list[ChatMessage],
) -> None:
    """No api_key means 'api_key' is absent from litellm kwargs."""
    config = make_ollama_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("ollama")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "llama")

    kwargs = mock_call.call_args.kwargs
    assert "api_key" not in kwargs


async def test_localhost_base_url(
    user_messages: list[ChatMessage],
) -> None:
    """Localhost base_url is forwarded as api_base."""
    config = make_ollama_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("ollama")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "llama")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["api_base"] == "http://localhost:11434"


async def test_zero_cost_model(
    user_messages: list[ChatMessage],
) -> None:
    """Zero-cost model produces cost_usd = 0.0."""
    config = make_ollama_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("ollama")

    mock_resp = build_model_response(prompt_tokens=5000, completion_tokens=2000)
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(user_messages, "llama")

    assert result.usage.cost_usd == 0.0
    assert result.usage.input_tokens == 5000
    assert result.usage.output_tokens == 2000


async def test_full_response_mapping(
    user_messages: list[ChatMessage],
) -> None:
    """Full Ollama response is correctly mapped."""
    config = make_ollama_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("ollama")

    mock_resp = build_model_response(
        content="Local LLM response",
        finish_reason="stop",
        request_id="ollama_req_001",
    )
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(user_messages, "llama")

    assert result.content == "Local LLM response"
    assert result.finish_reason == FinishReason.STOP
    assert result.model == "test-model-003"
    assert result.provider_request_id == "ollama_req_001"
