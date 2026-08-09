"""Unit tests for LiteLLMDriver image generation and the response mapper.

All tests mock ``litellm.aimage_generation`` -- no real API calls.
"""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.drivers.litellm_driver import LiteLLMDriver
from synthorg.providers.drivers.litellm_image import map_image_response
from synthorg.providers.errors import (
    AuthenticationError,
    ProviderError,
    ProviderInternalError,
)
from synthorg.providers.image_models import ImageGenerationConfig

from .conftest import make_credential_catalog, make_provider_config

pytestmark = pytest.mark.unit

_PATCH_IMAGE = "synthorg.providers.drivers.litellm_driver._litellm.aimage_generation"


def _image_model(cost_per_image: float | None = 0.04) -> ProviderModelConfig:
    return ProviderModelConfig(
        id="example-image-001",
        alias="img",
        cost_per_image=cost_per_image,
        max_context=1,
        metadata=ModelMetadata(
            supports_image_generation=True, metadata_source="preset"
        ),
    )


def _driver(cost_per_image: float | None = 0.04) -> LiteLLMDriver:
    config = make_provider_config(models=(_image_model(cost_per_image),))
    return LiteLLMDriver(
        "example-provider", config, connection_catalog=make_credential_catalog()
    )


def _fake_image_response(*b64: str) -> SimpleNamespace:
    return SimpleNamespace(
        data=[
            SimpleNamespace(b64_json=payload, url=None, revised_prompt="revised")
            for payload in b64
        ],
        usage=None,
    )


def test_map_image_response_maps_b64_and_cost() -> None:
    response = _fake_image_response("QUJD", "REVG")
    mapped = map_image_response(
        response, model_id="example-image-001", cost_per_image=0.04
    )
    assert len(mapped.images) == 2
    assert mapped.images[0].b64_data == "QUJD"
    assert mapped.images[0].revised_prompt == "revised"
    assert mapped.usage.cost == pytest.approx(0.08)
    assert mapped.model == "example-image-001"


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0d", "image/png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"GIF89a\x01\x00\x01\x00\x00", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"not-an-image-header-bytes", "image/png"),
    ],
    ids=["png", "jpeg", "gif", "webp", "unknown_defaults_png"],
)
def test_map_image_response_sniffs_content_type(head: bytes, expected: str) -> None:
    b64 = base64.b64encode(head).decode("ascii")
    mapped = map_image_response(
        _fake_image_response(b64), model_id="m", cost_per_image=0.0
    )
    assert mapped.images[0].content_type == expected


def test_map_image_response_rejects_url_only() -> None:
    response = SimpleNamespace(
        data=[
            SimpleNamespace(b64_json=None, url="https://x/y.png", revised_prompt=None),
        ]
    )
    with pytest.raises(ProviderInternalError):
        map_image_response(response, model_id="m", cost_per_image=0.0)


async def test_driver_generate_image_maps_response() -> None:
    driver = _driver()
    with patch(_PATCH_IMAGE, new_callable=AsyncMock) as mock_call:
        mock_call.return_value = _fake_image_response("QUJD")
        result = await driver.generate_image(
            "a cat", "img", config=ImageGenerationConfig(size="512x512", n=1)
        )
    assert result.images[0].b64_data == "QUJD"
    assert result.usage.cost == pytest.approx(0.04)
    # b64_json inline format requested so images arrive as bytes.
    _, kwargs = mock_call.call_args
    assert kwargs["response_format"] == "b64_json"
    assert kwargs["size"] == "512x512"


async def test_driver_generate_image_none_cost_is_free() -> None:
    driver = _driver(cost_per_image=None)
    with patch(_PATCH_IMAGE, new_callable=AsyncMock) as mock_call:
        mock_call.return_value = _fake_image_response("QUJD")
        result = await driver.generate_image("a cat", "img")
    assert result.usage.cost == 0.0


async def test_driver_maps_litellm_auth_error() -> None:
    from litellm.exceptions import AuthenticationError as LiteLLMAuthError

    driver = _driver()
    with patch(_PATCH_IMAGE, new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = LiteLLMAuthError(
            "bad key", llm_provider="example-provider", model="img"
        )
        with pytest.raises(AuthenticationError) as exc_info:
            await driver.generate_image("a cat", "img")
    assert isinstance(exc_info.value, ProviderError)


async def test_driver_maps_litellm_rate_limit_error() -> None:
    from litellm.exceptions import RateLimitError as LiteLLMRateLimitError

    driver = _driver()
    with patch(_PATCH_IMAGE, new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = LiteLLMRateLimitError(
            "slow down", llm_provider="example-provider", model="img"
        )
        # The new call site must forward non-auth provider exceptions through
        # the same mapper, not just the one exception type already covered.
        with pytest.raises(ProviderError):
            await driver.generate_image("a cat", "img")
