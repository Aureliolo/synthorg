"""Tests for the concrete planning-tool provider (web research grant)."""

import pytest

from synthorg.engine.decomposition.agent_session import _READ_ONLY_ACTION_TYPES
from synthorg.engine.decomposition.planning_tool_provider import PlanningToolProvider
from synthorg.engine.decomposition.tool_provider import DecompositionToolProvider
from synthorg.tools.web.web_search import SearchFilters, SearchResult

pytestmark = pytest.mark.unit


class _StubSearch:
    """Minimal WebSearchProvider for grant tests."""

    async def search(
        self,
        query: str,
        max_results: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[SearchResult]:
        del query, max_results, filters
        return []

    def unsupported_filters(self, filters: SearchFilters | None) -> tuple[str, ...]:
        """This stub applies whatever it is asked for."""
        del filters
        return ()


def test_satisfies_protocol() -> None:
    provider = PlanningToolProvider(search_provider=_StubSearch())
    assert isinstance(provider, DecompositionToolProvider)


def test_no_tools_when_search_unconfigured() -> None:
    provider = PlanningToolProvider(search_provider=None)
    assert provider.build_tools(owner_id="o", project_id="p") == ()


def test_grants_web_search_when_configured() -> None:
    provider = PlanningToolProvider(search_provider=_StubSearch())
    tools = provider.build_tools(owner_id="o", project_id=None)
    assert [t.name for t in tools] == ["web_search"]


def test_granted_web_search_survives_read_only_filter() -> None:
    """The grant is pointless if the planning session then drops it.

    web_search must carry a read-only action type so the agent-session
    strategy's read-only filter keeps it.
    """
    provider = PlanningToolProvider(search_provider=_StubSearch())
    (tool,) = provider.build_tools(owner_id="o", project_id=None)
    assert tool.action_type in _READ_ONLY_ACTION_TYPES
