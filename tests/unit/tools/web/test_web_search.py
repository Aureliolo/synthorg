"""Unit tests for WebSearchTool."""

import pytest
from pydantic import ValidationError

from synthorg.tools.web.web_search import SearchResult, WebSearchTool

from .conftest import MockSearchProvider


class TestWebSearchTool:
    """Tests for web search execution."""

    @pytest.mark.unit
    async def test_successful_search(self, mock_results: list[SearchResult]) -> None:
        provider = MockSearchProvider(results=mock_results)
        tool = WebSearchTool(provider=provider)
        result = await tool.execute(arguments={"query": "test query"})

        assert result.is_error is False
        assert "Test Result 1" in result.content
        assert "Test Result 2" in result.content
        assert result.metadata["result_count"] == 2

    @pytest.mark.unit
    async def test_empty_results(self) -> None:
        provider = MockSearchProvider(results=[])
        tool = WebSearchTool(provider=provider)
        result = await tool.execute(arguments={"query": "nothing"})

        assert result.is_error is False
        assert "no results" in result.content.lower()

    @pytest.mark.unit
    async def test_provider_error(self) -> None:
        provider = MockSearchProvider(error=RuntimeError("API error"))
        tool = WebSearchTool(provider=provider)
        result = await tool.execute(arguments={"query": "broken"})

        assert result.is_error is True
        assert "failed" in result.content.lower()

    @pytest.mark.unit
    async def test_max_results_passed_to_provider(
        self, mock_results: list[SearchResult]
    ) -> None:
        provider = MockSearchProvider(results=mock_results)
        tool = WebSearchTool(provider=provider)
        result = await tool.execute(arguments={"query": "test", "max_results": 1})

        assert result.is_error is False
        assert "Test Result 1" in result.content
        assert "Test Result 2" not in result.content
        assert result.metadata["result_count"] == 1


class TestSearchResult:
    """Tests for the SearchResult model."""

    @pytest.mark.unit
    def test_frozen(self) -> None:
        sr = SearchResult(title="T", url="U", snippet="S")
        with pytest.raises(Exception):  # noqa: B017, PT011
            sr.title = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_extra_field_rejected(self) -> None:
        """Unknown fields are rejected (extra='forbid')."""
        with pytest.raises(ValidationError, match="extra"):
            SearchResult(
                title="T",
                url="U",
                snippet="S",
                unknown_field="surprise",  # type: ignore[call-arg]
            )
