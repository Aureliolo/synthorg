"""Integration tests: gateway-shaped provider end-to-end pipeline.

A gateway provider is one reached through a custom ``base_url`` that
prefixes the model id with the provider name, serving several models under
one connection. Verifies base_url forwarding, model prefixing, and
multi-model alias resolution through the full pipeline.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.providers.models import ChatMessage

from .conftest import (
    build_model_response,
    make_catalog_with_key,
    make_gateway_config,
    make_gateway_registry,
)

pytestmark = pytest.mark.integration
_PATCH_TARGET = "synthorg.providers.drivers.litellm_driver._litellm.acompletion"
_PROVIDER = "gateway-provider"


async def test_base_url_forwarded(
    user_messages: list[ChatMessage],
) -> None:
    """Custom base_url is forwarded as api_base."""
    registry = await make_gateway_registry()
    driver = registry.get(_PROVIDER)

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "gateway-medium")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["api_base"] == "https://gateway.example/api/v1"


async def test_model_prefixed(
    user_messages: list[ChatMessage],
) -> None:
    """Model ID is prefixed with the provider name."""
    registry = await make_gateway_registry()
    driver = registry.get(_PROVIDER)

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "gateway-medium")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == f"{_PROVIDER}/test-model-gateway-001"


async def test_api_key_forwarded(
    user_messages: list[ChatMessage],
) -> None:
    """API key from the gateway connection is forwarded."""
    config = make_gateway_config()
    catalog = await make_catalog_with_key("provider-gateway-test", "sk-gw-test-key")
    registry = ProviderRegistry.from_config(config, connection_catalog=catalog)
    driver = registry.get(_PROVIDER)

    mock_resp = build_model_response()
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        await driver.complete(user_messages, "gateway-medium")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["api_key"] == "sk-gw-test-key"


async def test_full_response_mapping(
    user_messages: list[ChatMessage],
) -> None:
    """Full response is correctly mapped through the pipeline."""
    registry = await make_gateway_registry()
    driver = registry.get(_PROVIDER)

    mock_resp = build_model_response(
        content="Gateway response",
        prompt_tokens=200,
        completion_tokens=100,
        request_id="gw_req_001",
    )
    with patch(_PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp):
        result = await driver.complete(user_messages, "gateway-medium")

    assert result.content == "Gateway response"
    assert result.finish_reason == FinishReason.STOP
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 100
    assert result.provider_request_id == "gw_req_001"


async def test_multi_model_alias_resolution(
    user_messages: list[ChatMessage],
) -> None:
    """The second model resolves via its alias and computes cost."""
    registry = await make_gateway_registry()
    driver = registry.get(_PROVIDER)

    mock_resp = build_model_response(
        model="test-model-gateway-002",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    with patch(
        _PATCH_TARGET, new_callable=AsyncMock, return_value=mock_resp
    ) as mock_call:
        result = await driver.complete(user_messages, "gateway-small")

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == f"{_PROVIDER}/test-model-gateway-002"
    # (1000/1000)*0.0008 + (1000/1000)*0.0008 = 0.0016
    assert result.usage.cost == pytest.approx(0.0016)
