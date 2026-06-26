"""Unit tests for HtmlParserTool."""

import pytest
from pydantic import ValidationError

from synthorg.tools.web.html_parser import HtmlParserTool


class TestHtmlParserTextExtraction:
    """Tests for text extraction mode."""

    @pytest.mark.unit
    async def test_extract_text(self, html_tool: HtmlParserTool) -> None:
        html = "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
        result = await html_tool.execute(
            arguments={"html_content": html, "extract_mode": "text"}
        )
        assert result.is_error is False
        assert "Title" in result.content
        assert "Hello world" in result.content

    @pytest.mark.unit
    async def test_strips_script_tags(self, html_tool: HtmlParserTool) -> None:
        html = "<p>visible</p><script>alert('xss')</script><p>also visible</p>"
        result = await html_tool.execute(
            arguments={"html_content": html, "extract_mode": "text"}
        )
        assert "visible" in result.content
        assert "also visible" in result.content
        assert "alert" not in result.content

    @pytest.mark.unit
    async def test_strips_style_tags(self, html_tool: HtmlParserTool) -> None:
        html = "<p>text</p><style>body { color: red; }</style>"
        result = await html_tool.execute(
            arguments={"html_content": html, "extract_mode": "text"}
        )
        assert "text" in result.content
        assert "color" not in result.content

    @pytest.mark.unit
    async def test_empty_html(self, html_tool: HtmlParserTool) -> None:
        result = await html_tool.execute(
            arguments={"html_content": "", "extract_mode": "text"}
        )
        assert result.is_error is False
        assert "no content" in result.content.lower()


class TestHtmlParserLinkExtraction:
    """Tests for link extraction mode."""

    @pytest.mark.unit
    async def test_extract_links(self, html_tool: HtmlParserTool) -> None:
        html = '<a href="https://example.com">Example</a> <a href="/page">Page</a>'
        result = await html_tool.execute(
            arguments={"html_content": html, "extract_mode": "links"}
        )
        assert result.is_error is False
        # Output format: "- [text](href)"
        assert "(https://example.com)" in result.content
        assert "[Example]" in result.content
        assert "(/page)" in result.content

    @pytest.mark.unit
    async def test_no_links(self, html_tool: HtmlParserTool) -> None:
        html = "<p>No links here</p>"
        result = await html_tool.execute(
            arguments={"html_content": html, "extract_mode": "links"}
        )
        assert "no content" in result.content.lower()


class TestHtmlParserMetadataExtraction:
    """Tests for metadata extraction mode."""

    @pytest.mark.unit
    async def test_extract_title_and_meta(self, html_tool: HtmlParserTool) -> None:
        html = """
        <html><head>
            <title>Test Page</title>
            <meta name="description" content="A test page">
            <meta property="og:title" content="OG Title">
        </head></html>
        """
        result = await html_tool.execute(
            arguments={"html_content": html, "extract_mode": "metadata"}
        )
        assert result.is_error is False
        assert "Test Page" in result.content
        assert "description" in result.content
        assert "A test page" in result.content
        assert "og:title" in result.content

    @pytest.mark.unit
    async def test_no_metadata(self, html_tool: HtmlParserTool) -> None:
        html = "<html><body><p>content only</p></body></html>"
        result = await html_tool.execute(
            arguments={"html_content": html, "extract_mode": "metadata"}
        )
        assert "no content" in result.content.lower()


class TestHtmlParserEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.unit
    async def test_invalid_mode(self, html_tool: HtmlParserTool) -> None:
        # ``extract_mode`` is a closed Literal on ``HtmlParserArgs``.
        with pytest.raises(ValidationError):
            await html_tool.execute(
                arguments={"html_content": "<p>hi</p>", "extract_mode": "invalid"}
            )

    @pytest.mark.unit
    async def test_default_mode_is_text(self, html_tool: HtmlParserTool) -> None:
        html = "<p>Hello</p>"
        result = await html_tool.execute(arguments={"html_content": html})
        assert result.is_error is False
        assert "Hello" in result.content
