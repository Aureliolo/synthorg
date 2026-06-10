"""Tests for the image generator tool."""

from typing import cast

import pytest

from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools.design.image_generator import (
    ImageGeneratorTool,
    ImageProvider,
    ImageResult,
)
from tests._shared import JsonDict

from .conftest import MockImageProvider


@pytest.mark.unit
class TestImageGeneratorTool:
    """Tests for ImageGeneratorTool."""

    def test_category_is_design(
        self,
        mock_provider: MockImageProvider,
    ) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        assert tool.category == ToolCategory.DESIGN

    def test_action_type_is_docs_write(
        self,
        mock_provider: MockImageProvider,
    ) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        assert tool.action_type == ActionType.DOCS_WRITE

    def test_name(self, mock_provider: MockImageProvider) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        assert tool.name == "image_generator"

    def test_parameters_schema_has_required_prompt(
        self,
        mock_provider: MockImageProvider,
    ) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        schema = tool.parameters_schema
        assert schema is not None
        assert "prompt" in cast(JsonDict, schema)["required"]

    async def test_execute_success(
        self,
        mock_provider: MockImageProvider,
    ) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        result = await tool.execute(arguments={"prompt": "A sunset over mountains"})
        assert not result.is_error
        assert "Image generated successfully" in result.content
        assert result.metadata["width"] == 1024
        assert result.metadata["height"] == 1024
        assert len(mock_provider.calls) == 1

    async def test_execute_passes_all_params(
        self,
        mock_provider: MockImageProvider,
    ) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        await tool.execute(
            arguments={
                "prompt": "test",
                "style": "sketch",
                "width": 512,
                "height": 768,
                "quality": "high",
            }
        )
        assert len(mock_provider.calls) == 1
        call = mock_provider.calls[0]
        assert call["prompt"] == "test"
        assert call["style"] == "sketch"
        assert call["width"] == 512
        assert call["height"] == 768
        assert call["quality"] == "high"

    async def test_execute_no_provider_returns_error(self) -> None:
        tool = ImageGeneratorTool(provider=None)
        result = await tool.execute(arguments={"prompt": "test"})
        assert result.is_error
        assert "No ImageProvider" in result.content

    async def test_execute_provider_error(
        self,
        failing_provider: MockImageProvider,
    ) -> None:
        tool = ImageGeneratorTool(provider=failing_provider)
        result = await tool.execute(arguments={"prompt": "test"})
        assert result.is_error
        assert "Image generation failed" in result.content

    async def test_execute_default_style_and_quality(
        self,
        mock_provider: MockImageProvider,
    ) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        await tool.execute(arguments={"prompt": "test"})
        assert len(mock_provider.calls) == 1
        call = mock_provider.calls[0]
        assert call["style"] == "realistic"
        assert call["quality"] == "standard"
        assert call["width"] == 1024
        assert call["height"] == 1024

    def test_mock_provider_satisfies_protocol(
        self,
        mock_provider: MockImageProvider,
    ) -> None:
        assert isinstance(mock_provider, ImageProvider)

    async def test_execute_custom_result(self) -> None:
        custom_result = ImageResult(
            data="AQID",
            content_type="image/jpeg",
            width=512,
            height=256,
        )
        provider = MockImageProvider(result=custom_result)
        tool = ImageGeneratorTool(provider=provider)
        result = await tool.execute(arguments={"prompt": "test"})
        assert not result.is_error
        assert result.metadata["content_type"] == "image/jpeg"
        assert result.metadata["width"] == 512
        assert result.metadata["height"] == 256

    @pytest.mark.parametrize(
        ("width", "height"),
        [(100, 1024), (1024, 100), (4096, 1024), (1024, 4096)],
        ids=["width_low", "height_low", "width_high", "height_high"],
    )
    async def test_execute_invalid_dimensions(
        self,
        mock_provider: MockImageProvider,
        width: int,
        height: int,
    ) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={"prompt": "test", "width": width, "height": height}
        )
        assert result.is_error
        assert "must be between" in result.content
        assert len(mock_provider.calls) == 0

    @pytest.mark.parametrize(
        ("key", "value", "expected_msg"),
        [
            ("style", "watercolor", "Invalid style"),
            ("quality", "ultra", "Invalid quality"),
        ],
        ids=["invalid_style", "invalid_quality"],
    )
    async def test_execute_invalid_enum(
        self,
        mock_provider: MockImageProvider,
        key: str,
        value: str,
        expected_msg: str,
    ) -> None:
        tool = ImageGeneratorTool(provider=mock_provider)
        result = await tool.execute(arguments={"prompt": "test", key: value})
        assert result.is_error
        assert expected_msg in result.content
        assert len(mock_provider.calls) == 0
