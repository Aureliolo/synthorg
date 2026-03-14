"""Integration tests: OpenRouter provider end-to-end pipeline.

Verifies custom ``base_url`` forwarding, model prefixing, and
multi-model alias resolution through the full pipeline.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.providers.enums import FinishReason
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.providers.models import ChatMessage

from .conftest import build_model_response, make_openrouter_config

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]

_PATCH_TARGET = "synthorg.providers.drivers.litellm_driver._litellm.acompletion"


async def test_base_url_forwarded(
    user_messages: list[ChatMessage],
) -> None:
    """Custom base_url is forwarded as api_base."""
    config = make_openrouter_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("openrouter")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "or-medium")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["api_base"] == "https://openrouter.ai/api/v1"


async def test_model_prefixed(
    user_messages: list[ChatMessage],
) -> None:
    """Model ID is prefixed with 'openrouter/'."""
    config = make_openrouter_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("openrouter")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "or-medium")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == "openrouter/test-model-openrouter-001"


async def test_api_key_forwarded(
    user_messages: list[ChatMessage],
) -> None:
    """API key from OpenRouter config is forwarded."""
    config = make_openrouter_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("openrouter")

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "or-medium")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["api_key"] == "sk-or-test-key"


async def test_full_response_mapping(
    user_messages: list[ChatMessage],
) -> None:
    """Full response is correctly mapped through the pipeline."""
    config = make_openrouter_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("openrouter")

    mock_resp = build_model_response(
        content="OpenRouter response",
        prompt_tokens=200,
        completion_tokens=100,
        request_id="or_req_001",
    )
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(user_messages, "or-medium")

    assert result.content == "OpenRouter response"
    assert result.finish_reason == FinishReason.STOP
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 100
    assert result.provider_request_id == "or_req_001"


async def test_multi_model_alias_resolution(
    user_messages: list[ChatMessage],
) -> None:
    """Second model (llama-70b) resolves via alias and computes cost."""
    config = make_openrouter_config()
    registry = ProviderRegistry.from_config(config)
    driver = registry.get("openrouter")

    mock_resp = build_model_response(
        model="test-model-openrouter-002",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        result = await driver.complete(user_messages, "llama-70b")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == "openrouter/test-model-openrouter-002"
    # (1000/1000)*0.0008 + (1000/1000)*0.0008 = 0.0016
    assert result.usage.cost_usd == pytest.approx(0.0016)
