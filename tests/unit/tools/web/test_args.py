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
    @pytest.mark.parametrize("value", [1, 50, 100])
    def test_max_results_valid(self, value: int) -> None:
        """Boundary values inside ``[1, 100]`` are accepted."""
        assert WebSearchArgs(query="x", max_results=value).max_results == value

    @pytest.mark.unit
    @pytest.mark.parametrize("value", [0, -1, 101, 1000])
    def test_max_results_invalid(self, value: int) -> None:
        """Values outside ``[1, 100]`` are rejected."""
        with pytest.raises(ValidationError):
            WebSearchArgs(query="x", max_results=value)

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
    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
    def test_method_valid(self, method: str) -> None:
        """All HttpMethod literals round-trip through validation."""
        args = HttpRequestArgs.model_validate(
            {"url": "https://x", "method": method},
        )
        assert args.method == method

    @pytest.mark.unit
    def test_method_invalid_rejected(self) -> None:
        """Methods outside the closed set are rejected."""
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
    @pytest.mark.parametrize("value", [0, 30, 300])
    def test_timeout_valid(self, value: int) -> None:
        """Boundary values inside ``[0, 300]`` are accepted."""
        assert HttpRequestArgs(url="https://x", timeout=value).timeout == value

    @pytest.mark.unit
    @pytest.mark.parametrize("value", [-1, -100, 301, 600])
    def test_timeout_invalid(self, value: int) -> None:
        """Values outside ``[0, 300]`` are rejected."""
        with pytest.raises(ValidationError):
            HttpRequestArgs(url="https://x", timeout=value)

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
    @pytest.mark.parametrize("mode", ["text", "links", "metadata"])
    def test_extract_mode_valid(self, mode: str) -> None:
        """All HtmlExtractMode literals round-trip through validation."""
        args = HtmlParserArgs.model_validate(
            {"html_content": "<p>x</p>", "extract_mode": mode},
        )
        assert args.extract_mode == mode

    @pytest.mark.unit
    def test_extract_mode_invalid_rejected(self) -> None:
        """Modes outside the closed set are rejected."""
        with pytest.raises(ValidationError):
            HtmlParserArgs.model_validate(
                {"html_content": "<p>x</p>", "extract_mode": "raw"},
            )

    @pytest.mark.unit
    def test_empty_html_content_allowed(self) -> None:
        """The parser accepts empty HTML; the args model mirrors that."""
        args = HtmlParserArgs(html_content="")
        assert args.html_content == ""
