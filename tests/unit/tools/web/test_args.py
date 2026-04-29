"""Tests for typed web tool argument models."""

import pytest
from pydantic import ValidationError

from synthorg.tools.web._args import (
    HtmlParserArgs,
    HttpRequestArgs,
    WebSearchArgs,
)


class TestWebSearchArgs:
    @pytest.mark.unit
    def test_default_max_results(self) -> None:
        args = WebSearchArgs(query="python")
        assert args.max_results == 10

    @pytest.mark.unit
    def test_max_results_bounds(self) -> None:
        WebSearchArgs(query="x", max_results=1)
        WebSearchArgs(query="x", max_results=100)
        with pytest.raises(ValidationError):
            WebSearchArgs(query="x", max_results=0)
        with pytest.raises(ValidationError):
            WebSearchArgs(query="x", max_results=101)

    @pytest.mark.unit
    def test_blank_query_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WebSearchArgs(query="   ")


class TestHttpRequestArgs:
    @pytest.mark.unit
    def test_minimal_construction(self) -> None:
        args = HttpRequestArgs(url="https://example.com")
        assert args.method == "GET"
        assert args.headers == {}
        assert args.body is None
        assert args.timeout is None

    @pytest.mark.unit
    def test_method_is_closed_literal(self) -> None:
        for method in ("GET", "POST", "PUT", "DELETE"):
            args = HttpRequestArgs.model_validate(
                {"url": "https://x", "method": method},
            )
            assert args.method == method
        with pytest.raises(ValidationError):
            HttpRequestArgs.model_validate(
                {"url": "https://x", "method": "PATCH"},
            )

    @pytest.mark.unit
    def test_lowercase_method_rejected(self) -> None:
        """The Literal is case-sensitive; the tool used to upper() and the
        previous schema documented uppercase only."""
        with pytest.raises(ValidationError):
            HttpRequestArgs.model_validate(
                {"url": "https://x", "method": "get"},
            )

    @pytest.mark.unit
    def test_headers_must_be_string_to_string(self) -> None:
        args = HttpRequestArgs(
            url="https://x",
            headers={"X-API-Key": "secret"},
        )
        assert args.headers == {"X-API-Key": "secret"}
        with pytest.raises(ValidationError):
            HttpRequestArgs.model_validate(
                {"url": "https://x", "headers": {"X-Count": 1}},
            )

    @pytest.mark.unit
    def test_timeout_bounds(self) -> None:
        HttpRequestArgs(url="https://x", timeout=0)
        HttpRequestArgs(url="https://x", timeout=300)
        with pytest.raises(ValidationError):
            HttpRequestArgs(url="https://x", timeout=-1)
        with pytest.raises(ValidationError):
            HttpRequestArgs(url="https://x", timeout=301)

    @pytest.mark.unit
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HttpRequestArgs.model_validate(
                {"url": "https://x", "smuggled": "field"},
            )


class TestHtmlParserArgs:
    @pytest.mark.unit
    def test_default_extract_mode(self) -> None:
        args = HtmlParserArgs(html_content="<p>x</p>")
        assert args.extract_mode == "text"

    @pytest.mark.unit
    def test_extract_mode_is_closed_literal(self) -> None:
        for mode in ("text", "links", "metadata"):
            args = HtmlParserArgs.model_validate(
                {"html_content": "<p>x</p>", "extract_mode": mode},
            )
            assert args.extract_mode == mode
        with pytest.raises(ValidationError):
            HtmlParserArgs.model_validate(
                {"html_content": "<p>x</p>", "extract_mode": "raw"},
            )

    @pytest.mark.unit
    def test_empty_html_content_allowed(self) -> None:
        """The parser accepts empty HTML; the args model mirrors that."""
        args = HtmlParserArgs(html_content="")
        assert args.html_content == ""
