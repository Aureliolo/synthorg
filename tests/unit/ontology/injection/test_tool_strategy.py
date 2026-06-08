"""Tests for ToolBasedInjectionStrategy and LookupEntityTool."""

from unittest.mock import AsyncMock

import pytest

from synthorg.ontology.injection.tool import (
    LOOKUP_ENTITY_TOOL_NAME,
    LookupEntityTool,
    ToolBasedInjectionStrategy,
)
from synthorg.security.autonomy.enums import ToolCategory


@pytest.mark.unit
class TestLookupEntityTool:
    """Tests for LookupEntityTool."""

    def test_tool_metadata(self, mock_backend: AsyncMock) -> None:
        """Tool has correct name, category, and description."""
        tool = LookupEntityTool(backend=mock_backend)
        assert tool.name == LOOKUP_ENTITY_TOOL_NAME
        assert tool.category == ToolCategory.ONTOLOGY
        assert "entity" in tool.description.lower()

    async def test_lookup_by_name_found(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Exact name lookup returns formatted entity."""
        tool = LookupEntityTool(backend=mock_backend)
        result = await tool.execute(arguments={"name": "Task"})
        assert not result.is_error
        assert "Task" in result.content

    async def test_lookup_by_name_not_found(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Missing entity returns error result."""
        tool = LookupEntityTool(backend=mock_backend)
        result = await tool.execute(arguments={"name": "Nonexistent"})
        assert result.is_error
        assert "not found" in result.content.lower()

    async def test_search_with_results(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Search query returns matching entities."""
        tool = LookupEntityTool(backend=mock_backend)
        result = await tool.execute(arguments={"query": "work"})
        assert not result.is_error
        assert "Task" in result.content

    async def test_search_no_results(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Search with no matches returns informative message."""
        tool = LookupEntityTool(backend=mock_backend)
        result = await tool.execute(arguments={"query": "zzzznothing"})
        assert result.is_error is False
        assert "No entities match" in result.content

    async def test_lookup_backend_error_returns_error(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Unexpected backend error in _lookup_by_name returns error."""
        mock_backend.get.side_effect = RuntimeError("connection lost")
        tool = LookupEntityTool(backend=mock_backend)
        result = await tool.execute(arguments={"name": "Task"})
        assert result.is_error
        assert "failed" in result.content.lower()

    async def test_search_backend_error_returns_error(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Unexpected backend error in _search returns error."""
        mock_backend.search.side_effect = RuntimeError("connection lost")
        tool = LookupEntityTool(backend=mock_backend)
        result = await tool.execute(arguments={"query": "work"})
        assert result.is_error
        assert "failed" in result.content.lower()

    async def test_no_arguments_returns_error(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Missing both name and query returns error."""
        tool = LookupEntityTool(backend=mock_backend)
        result = await tool.execute(arguments={})
        assert result.is_error

    async def test_both_name_and_query_returns_error(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Providing both name and query returns conflict error."""
        tool = LookupEntityTool(backend=mock_backend)
        result = await tool.execute(
            arguments={"name": "Task", "query": "search"},
        )
        assert result.is_error
        assert "exactly one" in result.content.lower()

    def test_to_definition(self, mock_backend: AsyncMock) -> None:
        """to_definition() returns valid ToolDefinition."""
        tool = LookupEntityTool(backend=mock_backend)
        defn = tool.to_definition()
        assert defn.name == LOOKUP_ENTITY_TOOL_NAME
        assert defn.parameters_schema

    def test_custom_tool_name(self, mock_backend: AsyncMock) -> None:
        """Custom tool name is respected."""
        tool = LookupEntityTool(
            backend=mock_backend,
            tool_name="get_entity",
        )
        assert tool.name == "get_entity"


@pytest.mark.unit
class TestToolBasedInjectionStrategy:
    """Tests for ToolBasedInjectionStrategy."""

    async def test_prepare_messages_returns_empty(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Tool strategy injects no messages."""
        strategy = ToolBasedInjectionStrategy(backend=mock_backend)
        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            task_context="Do work",
            token_budget=5000,
        )
        assert messages == ()

    def test_get_tool_definitions(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Returns the lookup_entity tool definition."""
        strategy = ToolBasedInjectionStrategy(backend=mock_backend)
        tools = strategy.get_tool_definitions()
        assert len(tools) == 1
        assert tools[0].name == LOOKUP_ENTITY_TOOL_NAME

    def test_strategy_name(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """Strategy name is 'tool'."""
        strategy = ToolBasedInjectionStrategy(backend=mock_backend)
        assert strategy.strategy_name == "tool"

    def test_tool_property(
        self,
        mock_backend: AsyncMock,
    ) -> None:
        """tool property returns the LookupEntityTool instance."""
        strategy = ToolBasedInjectionStrategy(backend=mock_backend)
        assert isinstance(strategy.tool, LookupEntityTool)
