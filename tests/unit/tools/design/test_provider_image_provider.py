"""Tests for the provider-backed ImageProvider adapter."""

import base64
from typing import override

import pytest

from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.errors import ProviderImageGenerationUnsupportedError
from synthorg.providers.image_models import (
    ImageGenerationConfig,
    ImageGenerationResponse,
)
from synthorg.tools.design.image_generator import ImageProvider, ImageResult
from synthorg.tools.design.provider_image_provider import ProviderImageProvider

pytestmark = pytest.mark.unit

_MODEL = "example-image-001"


def test_rejects_blank_model() -> None:
    with pytest.raises(ValueError, match="model"):
        ProviderImageProvider(provider=ScriptedDriver(), model="   ")


def test_adapter_satisfies_image_provider_protocol() -> None:
    adapter = ProviderImageProvider(provider=ScriptedDriver(), model=_MODEL)
    assert isinstance(adapter, ImageProvider)


async def test_adapter_maps_provider_response_to_image_result() -> None:
    adapter = ProviderImageProvider(provider=ScriptedDriver(), model=_MODEL)
    result = await adapter.generate(prompt="a cat", width=256, height=256)
    assert isinstance(result, ImageResult)
    assert result.width == 256
    assert result.height == 256
    # The scripted provider returns a real, decodable PNG.
    assert base64.b64decode(result.data, validate=True)


async def test_adapter_fails_closed_on_unsupported_provider() -> None:
    class _NoImageDriver(ScriptedDriver):
        @override
        async def _do_generate_image(
            self,
            prompt: str,
            model: str,
            *,
            config: ImageGenerationConfig | None = None,
        ) -> ImageGenerationResponse:
            msg = "nope"
            raise ProviderImageGenerationUnsupportedError(msg)

    adapter = ProviderImageProvider(provider=_NoImageDriver(), model="chat-only")
    with pytest.raises(ProviderImageGenerationUnsupportedError):
        await adapter.generate(prompt="a cat")
