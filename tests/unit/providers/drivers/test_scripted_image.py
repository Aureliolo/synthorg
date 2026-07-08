"""Tests for the scripted driver's deterministic offline image generation."""

import base64
from io import BytesIO

import pytest
from PIL import Image

from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.drivers.scripted_image import render_deterministic_png
from synthorg.providers.image_models import ImageGenerationConfig

pytestmark = pytest.mark.unit


def test_render_produces_valid_png_of_requested_size() -> None:
    png = render_deterministic_png("a cat", size="256x384")
    image = Image.open(BytesIO(png))
    assert image.format == "PNG"
    assert image.size == (256, 384)


def test_render_is_deterministic() -> None:
    a = render_deterministic_png("a cat", size="512x512")
    b = render_deterministic_png("a cat", size="512x512")
    assert a == b


def test_render_varies_by_prompt_and_index() -> None:
    base = render_deterministic_png("a cat", size="512x512")
    assert render_deterministic_png("a dog", size="512x512") != base
    assert render_deterministic_png("a cat", size="512x512", index=1) != base


async def test_scripted_driver_generates_n_valid_pngs() -> None:
    driver = ScriptedDriver()
    result = await driver.generate_image(
        "a sunset",
        "example-image-001",
        config=ImageGenerationConfig(n=2, size="256x256"),
    )
    assert len(result.images) == 2
    assert result.model == "example-image-001"
    for generated in result.images:
        decoded = base64.b64decode(generated.b64_data, validate=True)
        image = Image.open(BytesIO(decoded))
        assert image.format == "PNG"
        assert image.size == (256, 256)
    # The two images in a multi-image request differ (index-seeded).
    assert result.images[0].b64_data != result.images[1].b64_data


async def test_scripted_driver_image_cost_is_zero() -> None:
    driver = ScriptedDriver()
    result = await driver.generate_image("a sunset", "example-image-001")
    assert result.usage.cost == 0.0
