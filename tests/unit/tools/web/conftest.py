"""Shared fixtures for web tool tests."""

import pytest

from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.html_parser import HtmlParserTool
from synthorg.tools.web.http_request import HttpRequestTool
from synthorg.tools.web.web_search import SearchFilters, SearchResult


class MockSearchProvider:
    """Mock web search provider for testing."""

    def __init__(
        self,
        *,
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
        unsupported: tuple[str, ...] = (),
    ) -> None:
        self._results = results or []
        self._error = error
        self._unsupported = unsupported
        self.filters: SearchFilters | None = None

    async def search(
        self,
        query: str,
        max_results: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[SearchResult]:
        self.filters = filters
        if self._error:
            raise self._error
        return self._results[:max_results]

    def unsupported_filters(self, filters: SearchFilters | None) -> tuple[str, ...]:
        """Report the filters this double was told to refuse."""
        del filters
        return self._unsupported


@pytest.fixture
def permissive_policy() -> NetworkPolicy:
    """Policy that allows all IPs (for testing HTTP logic)."""
    return NetworkPolicy(block_private_ips=False)


@pytest.fixture
def http_tool(permissive_policy: NetworkPolicy) -> HttpRequestTool:
    return HttpRequestTool(network_policy=permissive_policy)


@pytest.fixture
def html_tool() -> HtmlParserTool:
    return HtmlParserTool()


@pytest.fixture
def mock_results() -> list[SearchResult]:
    return [
        SearchResult(
            title="Test Result 1",
            url="https://example.com/1",
            snippet="First result snippet",
        ),
        SearchResult(
            title="Test Result 2",
            url="https://example.com/2",
            snippet="Second result snippet",
        ),
    ]
