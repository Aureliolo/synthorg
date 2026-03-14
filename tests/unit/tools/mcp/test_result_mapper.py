"""Tests for MCP result mapping (ADR-002 D18)."""

import pytest
from mcp.types import (
    AudioContent,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)

from synthorg.tools.mcp.models import MCPRawResult
from synthorg.tools.mcp.result_mapper import map_call_tool_result

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestTextContentMapping:
    """TextContent blocks map to content string."""

    def test_single_text(self) -> None:
        raw = MCPRawResult(
            content=(TextContent(type="text", text="hello"),),
        )
        result = map_call_tool_result(raw)
        assert result.content == "hello"
        assert not result.is_error

    def test_multiple_texts_joined(self) -> None:
        raw = MCPRawResult(
            content=(
                TextContent(type="text", text="line 1"),
                TextContent(type="text", text="line 2"),
            ),
        )
        result = map_call_tool_result(raw)
        assert result.content == "line 1\nline 2"

    def test_empty_text(self) -> None:
        raw = MCPRawResult(
            content=(TextContent(type="text", text=""),),
        )
        result = map_call_tool_result(raw)
        assert result.content == ""


class TestImageContentMapping:
    """ImageContent blocks produce placeholders and attachments."""

    def test_image_placeholder_and_attachment(self) -> None:
        raw = MCPRawResult(
            content=(
                ImageContent(
                    type="image",
                    data="base64data",
                    mimeType="image/png",
                ),
            ),
        )
        result = map_call_tool_result(raw)
        assert result.content == "[image: image/png]"
        attachments = result.metadata["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["type"] == "image"
        assert attachments[0]["mimeType"] == "image/png"
        assert attachments[0]["data"] == "base64data"


class TestAudioContentMapping:
    """AudioContent blocks produce placeholders and attachments."""

    def test_audio_placeholder_and_attachment(self) -> None:
        raw = MCPRawResult(
            content=(
                AudioContent(
                    type="audio",
                    data="audiodata",
                    mimeType="audio/mp3",
                ),
            ),
        )
        result = map_call_tool_result(raw)
        assert result.content == "[audio: audio/mp3]"
        attachments = result.metadata["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["type"] == "audio"
        assert attachments[0]["mimeType"] == "audio/mp3"
        assert attachments[0]["data"] == "audiodata"


class TestEmbeddedResourceMapping:
    """EmbeddedResource blocks produce resource placeholders."""

    def test_resource_placeholder(self) -> None:
        resource = TextResourceContents(
            uri="file:///test.txt",  # type: ignore[arg-type]
            text="file content",
        )
        raw = MCPRawResult(
            content=(
                EmbeddedResource(
                    type="resource",
                    resource=resource,
                ),
            ),
        )
        result = map_call_tool_result(raw)
        assert result.content == "[resource: file:///test.txt]"
        assert "attachments" not in result.metadata


class TestStructuredContent:
    """structuredContent maps to metadata."""

    def test_structured_content_in_metadata(self) -> None:
        raw = MCPRawResult(
            content=(TextContent(type="text", text="ok"),),
            structured_content={"key": "value"},
        )
        result = map_call_tool_result(raw)
        assert result.metadata["structured_content"] == {"key": "value"}

    def test_no_structured_content(self) -> None:
        raw = MCPRawResult(
            content=(TextContent(type="text", text="ok"),),
        )
        result = map_call_tool_result(raw)
        assert "structured_content" not in result.metadata


class TestIsErrorMapping:
    """isError maps 1:1 to is_error."""

    def test_error_true(self) -> None:
        raw = MCPRawResult(
            content=(TextContent(type="text", text="err"),),
            is_error=True,
        )
        result = map_call_tool_result(raw)
        assert result.is_error

    def test_error_false(self) -> None:
        raw = MCPRawResult(
            content=(TextContent(type="text", text="ok"),),
            is_error=False,
        )
        result = map_call_tool_result(raw)
        assert not result.is_error


class TestEmptyContent:
    """Empty content produces empty string."""

    def test_empty_content_tuple(self) -> None:
        raw = MCPRawResult(content=())
        result = map_call_tool_result(raw)
        assert result.content == ""
        assert not result.is_error


class TestUnknownContentBlock:
    """Unknown content block types produce placeholders."""

    def test_unknown_block_produces_placeholder(self) -> None:
        from unittest.mock import MagicMock

        unknown = MagicMock()
        type(unknown).__name__ = "MysteryBlock"
        raw = MCPRawResult(content=(unknown,))
        result = map_call_tool_result(raw)
        assert "[unknown: MysteryBlock]" in result.content
        assert "attachments" not in result.metadata


class TestMixedContent:
    """Mixed content types in a single result."""

    def test_text_and_image_mixed(self) -> None:
        raw = MCPRawResult(
            content=(
                TextContent(type="text", text="header"),
                ImageContent(
                    type="image",
                    data="imgdata",
                    mimeType="image/jpeg",
                ),
                TextContent(type="text", text="footer"),
            ),
        )
        result = map_call_tool_result(raw)
        lines = result.content.split("\n")
        assert lines[0] == "header"
        assert lines[1] == "[image: image/jpeg]"
        assert lines[2] == "footer"
        assert len(result.metadata["attachments"]) == 1

    def test_image_and_audio_combined(self) -> None:
        raw = MCPRawResult(
            content=(
                ImageContent(
                    type="image",
                    data="img",
                    mimeType="image/png",
                ),
                AudioContent(
                    type="audio",
                    data="aud",
                    mimeType="audio/wav",
                ),
            ),
        )
        result = map_call_tool_result(raw)
        assert "[image: image/png]" in result.content
        assert "[audio: audio/wav]" in result.content
        assert len(result.metadata["attachments"]) == 2
