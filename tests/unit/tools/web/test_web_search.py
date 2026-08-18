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


class TestFiltersReachTheProvider:
    """The tool's half of the filter contract.

    The translation into each vendor's vocabulary is the provider's job and is
    tested there. What is tested here is that the arguments an agent wrote
    arrive as filters at all, and that a filter the provider cannot express is
    named back to the agent rather than dropped in silence.
    """

    @pytest.mark.unit
    async def test_recency_reaches_the_provider(
        self,
        mock_results: list[SearchResult],
    ) -> None:
        provider = MockSearchProvider(results=mock_results)
        await WebSearchTool(provider=provider).execute(
            arguments={"query": "current api", "recency": "week"},
        )
        assert provider.filters is not None
        assert provider.filters.recency == "week"

    @pytest.mark.unit
    async def test_domain_filters_reach_the_provider(
        self,
        mock_results: list[SearchResult],
    ) -> None:
        provider = MockSearchProvider(results=mock_results)
        await WebSearchTool(provider=provider).execute(
            arguments={
                "query": "widget docs",
                "include_domains": ["docs.example-provider.test"],
                "exclude_domains": ["spam.example.test"],
            },
        )
        assert provider.filters is not None
        assert provider.filters.include_domains == ("docs.example-provider.test",)
        assert provider.filters.exclude_domains == ("spam.example.test",)

    @pytest.mark.unit
    async def test_no_filters_is_reported_as_no_filters(
        self,
        mock_results: list[SearchResult],
    ) -> None:
        provider = MockSearchProvider(results=mock_results)
        await WebSearchTool(provider=provider).execute(arguments={"query": "plain"})
        assert provider.filters is None or provider.filters.is_empty


class TestUnsupportedFiltersAreNamed:
    """A silently dropped filter returns stale results that look filtered."""

    @pytest.mark.unit
    async def test_the_content_names_the_dropped_filter(
        self,
        mock_results: list[SearchResult],
    ) -> None:
        provider = MockSearchProvider(results=mock_results, unsupported=("recency",))
        result = await WebSearchTool(provider=provider).execute(
            arguments={"query": "current api", "recency": "week"},
        )
        assert result.is_error is False
        assert "recency" in result.content

    @pytest.mark.unit
    async def test_the_metadata_names_the_dropped_filter(
        self,
        mock_results: list[SearchResult],
    ) -> None:
        provider = MockSearchProvider(
            results=mock_results,
            unsupported=("recency", "include_domains"),
        )
        result = await WebSearchTool(provider=provider).execute(
            arguments={
                "query": "current api",
                "recency": "week",
                "include_domains": ["docs.example-provider.test"],
            },
        )
        assert result.metadata["unsupported_filters"] == [
            "recency",
            "include_domains",
        ]

    @pytest.mark.unit
    async def test_a_fully_supported_search_says_nothing_about_filters(
        self,
        mock_results: list[SearchResult],
    ) -> None:
        provider = MockSearchProvider(results=mock_results, unsupported=())
        result = await WebSearchTool(provider=provider).execute(
            arguments={"query": "current api", "recency": "week"},
        )
        assert result.metadata["unsupported_filters"] == []
        assert "could not" not in result.content.lower()


class TestArgumentValidation:
    """The typed boundary, which is what keeps a bad filter off the wire.

    Rejection is a raise rather than an error result: the arguments never
    became a call, so there is nothing for the provider to have been asked. In
    each case the provider must not have been reached at all.
    """

    @pytest.mark.unit
    async def test_an_unknown_recency_window_is_refused(self) -> None:
        provider = MockSearchProvider(results=[])
        with pytest.raises(ValidationError, match="recency"):
            await WebSearchTool(provider=provider).execute(
                arguments={"query": "q", "recency": "fortnight"},
            )
        assert provider.filters is None

    @pytest.mark.unit
    async def test_a_blank_domain_is_refused(self) -> None:
        provider = MockSearchProvider(results=[])
        with pytest.raises(ValidationError, match="include_domains"):
            await WebSearchTool(provider=provider).execute(
                arguments={"query": "q", "include_domains": [""]},
            )
        assert provider.filters is None

    @pytest.mark.unit
    async def test_too_many_domains_are_refused(self) -> None:
        """The cap exists so one call cannot build an unbounded request."""
        provider = MockSearchProvider(results=[])
        with pytest.raises(ValidationError, match="at most 20"):
            await WebSearchTool(provider=provider).execute(
                arguments={
                    "query": "q",
                    "include_domains": [f"d{i}.example.test" for i in range(25)],
                },
            )
        assert provider.filters is None


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
