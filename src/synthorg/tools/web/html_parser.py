"""HTML parser tool -- extract content from HTML.

Operates on pre-fetched HTML content (no HTTP requests).  Supports
text extraction, link extraction, and metadata extraction using the
stdlib ``html.parser`` module.
"""

from html.parser import HTMLParser
from typing import Any, ClassVar, Final

from pydantic import BaseModel

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ActionType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_PARSE_FAILED,
    WEB_PARSE_START,
    WEB_PARSE_SUCCESS,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.web._args import HtmlParserArgs
from synthorg.tools.web.base_web_tool import BaseWebTool

logger = get_logger(__name__)

_EXTRACT_MODES: Final[tuple[str, ...]] = ("text", "links", "metadata")


# ── Extraction helpers ─────────────────────────────────────────


class _TextExtractor(HTMLParser):
    """Extract visible text from HTML, skipping script/style tags."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],  # noqa: ARG002
    ) -> None:
        """Handle starttag."""
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """Handle endtag."""
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        """Handle data."""
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    def get_text(self) -> str:
        """Return extracted text joined by newlines.

        Returns:
            Result of type ``str``.
        """
        return "\n".join(self._chunks)


class _LinkExtractor(HTMLParser):
    """Extract all href links from anchor tags."""

    def __init__(self) -> None:
        super().__init__()
        self._links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle starttag."""
        if tag == "a":
            href = dict(attrs).get("href")
            self._current_href = href or None
            self._current_text_chunks = []

    def handle_data(self, data: str) -> None:
        """Handle data."""
        if self._current_href is not None:
            stripped = data.strip()
            if stripped:
                self._current_text_chunks.append(stripped)

    def handle_endtag(self, tag: str) -> None:
        """Handle endtag."""
        if tag == "a" and self._current_href is not None:
            text = " ".join(self._current_text_chunks) or self._current_href
            self._links.append((self._current_href, text))
            self._current_href = None
            self._current_text_chunks = []

    def get_links(self) -> list[tuple[str, str]]:
        """Return extracted (href, text) pairs.

        Returns:
            List of ``tuple[str, str]``.
        """
        return list(self._links)


class _MetadataExtractor(HTMLParser):
    """Extract title and meta tags from HTML head."""

    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._title_chunks: list[str] = []
        self._meta: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle starttag."""
        if tag == "title":
            self._in_title = True
            self._title_chunks = []
        elif tag == "meta":
            attr_dict = dict(attrs)
            name = attr_dict.get("name") or attr_dict.get("property", "")
            content = attr_dict.get("content", "")
            if name and content:
                self._meta.append((name, content))

    def handle_data(self, data: str) -> None:
        """Handle data."""
        if self._in_title:
            self._title_chunks.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        """Handle endtag."""
        if tag == "title":
            self._in_title = False

    def get_title(self) -> str:
        """Return the page title.

        Returns:
            Result of type ``str``.
        """
        return " ".join(self._title_chunks)

    def get_meta(self) -> list[tuple[str, str]]:
        """Return extracted (name, content) pairs.

        Returns:
            List of ``tuple[str, str]``.
        """
        return list(self._meta)


# ── Tool ───────────────────────────────────────────────────────


class HtmlParserTool(BaseWebTool):
    """Parse HTML content and extract text, links, or metadata.

    Operates on pre-fetched HTML content -- does not make HTTP
    requests.  Uses the stdlib ``html.parser`` module for extraction.

    Examples:
        Extract text from HTML::

            tool = HtmlParserTool()
            result = await tool.execute(
                arguments={
                    "html_content": "<p>Hello world</p>",
                    "extract_mode": "text",
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = HtmlParserArgs

    def __init__(self) -> None:
        """Initialize the HTML parser tool with no extra dependencies.

        The parser is stateless; HTML parsing safety (XXE, billion
        laughs) is enforced by :class:`HTMLParseGuard`.
        """
        super().__init__(
            name="html_parser",
            description=(
                "Parse HTML content and extract text, links, "
                "or metadata (title, meta tags)."
            ),
            parameters_schema=HtmlParserArgs.model_json_schema(),
            action_type=ActionType.CODE_READ,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Parse HTML and extract content.

        Args:
            arguments: Must contain ``html_content``; optionally
                ``extract_mode`` (text, links, or metadata).

        Returns:
            A ``ToolExecutionResult`` with extracted content.
        """
        html_content: str = arguments["html_content"]
        mode: str = arguments.get("extract_mode", "text")

        if mode not in _EXTRACT_MODES:
            logger.warning(
                WEB_PARSE_FAILED,
                mode=mode,
                error=f"Invalid extract_mode: {mode!r}",
                valid_modes=_EXTRACT_MODES,
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid extract_mode: {mode!r}. Must be one of: {_EXTRACT_MODES}"
                ),
                is_error=True,
            )

        logger.info(WEB_PARSE_START, mode=mode, content_length=len(html_content))

        try:
            if mode == "text":
                content = self._extract_text(html_content)
            elif mode == "links":
                content = self._extract_links(html_content)
            else:
                content = self._extract_metadata(html_content)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                WEB_PARSE_FAILED,
                mode=mode,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"HTML parsing failed: {safe_error_description(exc)}",
                is_error=True,
            )

        logger.info(
            WEB_PARSE_SUCCESS,
            mode=mode,
            output_length=len(content),
        )
        return ToolExecutionResult(
            content=content or "(no content extracted)",
            metadata={"mode": mode},
        )

    @staticmethod
    def _extract_text(html: str) -> str:
        """Extract visible text from HTML.

        Returns:
            Result of type ``str``.
        """
        extractor = _TextExtractor()
        extractor.feed(html)
        extractor.close()
        return extractor.get_text()

    @staticmethod
    def _extract_links(html: str) -> str:
        """Extract links from HTML and format as list.

        Returns:
            Result of type ``str``.
        """
        extractor = _LinkExtractor()
        extractor.feed(html)
        extractor.close()
        links = extractor.get_links()
        if not links:
            return ""
        lines = [f"- [{text}]({href})" for href, text in links]
        return "\n".join(lines)

    @staticmethod
    def _extract_metadata(html: str) -> str:
        """Extract title and meta tags from HTML.

        Returns:
            Result of type ``str``.
        """
        extractor = _MetadataExtractor()
        extractor.feed(html)
        extractor.close()
        parts: list[str] = []
        title = extractor.get_title()
        if title:
            parts.append(f"Title: {title}")
        for name, content in extractor.get_meta():
            parts.append(f"{name}: {content}")
        return "\n".join(parts)
