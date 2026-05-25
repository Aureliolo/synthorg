# mypy: disable-error-code="explicit-any,explicit-override"
"""Tests for memory BaseTool wrappers and ToolRegistry integration."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import MemoryCategory, ToolCategory
from synthorg.memory.injection import InjectionStrategy, MemoryInjectionStrategy
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
from synthorg.memory.tools import (
    RecallMemoryTool,
    SearchMemoryTool,
    _is_error_response,
    create_memory_tools,
    registry_with_memory_tools,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.registry import ToolRegistry

# -- Fixtures ---------------------------------------------------------------


def _make_entry(
    *,
    entry_id: str = "mem-1",
    agent_id: str = "agent-1",
    content: str = "test memory content",
    relevance_score: float | None = 0.85,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=agent_id,
        category=MemoryCategory.EPISODIC,
        content=content,
        metadata=MemoryMetadata(),
        created_at=datetime.now(UTC),
        relevance_score=relevance_score,
    )


def _make_backend(
    entries: tuple[MemoryEntry, ...] = (),
) -> AsyncMock:
    backend = AsyncMock(spec=MemoryBackend)
    backend.retrieve = AsyncMock(return_value=entries)
    backend.get = AsyncMock(return_value=entries[0] if entries else None)
    return backend


def _tool_config() -> MemoryRetrievalConfig:
    return MemoryRetrievalConfig(
        strategy=InjectionStrategy.TOOL_BASED,
        min_relevance=0.0,
    )


def _make_strategy(
    entries: tuple[MemoryEntry, ...] = (),
) -> ToolBasedInjectionStrategy:
    return ToolBasedInjectionStrategy(
        backend=_make_backend(entries),
        config=_tool_config(),
    )


def _make_empty_registry() -> ToolRegistry:
    return ToolRegistry([])


class _DummyTool(BaseTool):
    """Minimal concrete tool for testing registry augmentation."""

    def __init__(self, name: str = "dummy_tool") -> None:
        super().__init__(
            name=name,
            category=ToolCategory.OTHER,
        )

    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(content="dummy")


# -- SearchMemoryTool -------------------------------------------------------


@pytest.mark.unit
class TestSearchMemoryTool:
    def test_is_base_tool(self) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert isinstance(tool, BaseTool)

    def test_name(self) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert tool.name == "search_memory"

    def test_category_is_memory(self) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert tool.category == ToolCategory.MEMORY

    def test_has_description(self) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert tool.description
        assert "memory" in tool.description.lower()

    def test_has_parameters_schema(self) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        schema = tool.parameters_schema
        assert schema is not None
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "limit" in schema["properties"]
        assert "categories" in schema["properties"]
        assert "query" in schema["required"]

    def test_schema_is_deep_copied(self) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        schema_a = tool.parameters_schema
        schema_b = tool.parameters_schema
        assert schema_a is not schema_b
        assert schema_a == schema_b

    async def test_execute_delegates_to_strategy(self) -> None:
        entry = _make_entry(content="found via search")
        strategy = _make_strategy((entry,))
        tool = SearchMemoryTool(strategy=strategy, agent_id="agent-1")

        result = await tool.execute(arguments={"query": "test search"})

        assert isinstance(result, ToolExecutionResult)
        assert "found via search" in result.content
        assert not result.is_error

    @pytest.mark.parametrize("query", ["", "   ", "\t\n"])
    async def test_execute_blank_query_returns_error(self, query: str) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        result = await tool.execute(arguments={"query": query})

        assert result.is_error

    async def test_execute_no_results(self) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        result = await tool.execute(arguments={"query": "nothing"})

        assert not result.is_error
        assert "no memories found" in result.content.lower()

    async def test_execute_with_categories(self) -> None:
        entry = _make_entry(content="categorized")
        strategy = _make_strategy((entry,))
        tool = SearchMemoryTool(strategy=strategy, agent_id="agent-1")

        result = await tool.execute(
            arguments={
                "query": "test",
                "categories": ["episodic"],
                "limit": 5,
            },
        )

        assert isinstance(result, ToolExecutionResult)
        assert "categorized" in result.content

    async def test_execute_backend_error_returns_error_result(self) -> None:
        from synthorg.memory.errors import MemoryRetrievalError

        backend = _make_backend()
        backend.retrieve = AsyncMock(
            side_effect=MemoryRetrievalError("connection lost"),
        )
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        tool = SearchMemoryTool(strategy=strategy, agent_id="agent-1")

        result = await tool.execute(arguments={"query": "will fail"})

        assert result.is_error
        assert "unavailable" in result.content.lower()
        # Must not leak internal error details
        assert "connection lost" not in result.content

    def test_to_definition_produces_valid_tool_definition(self) -> None:
        tool = SearchMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        defn = tool.to_definition()
        assert defn.name == "search_memory"
        assert defn.parameters_schema
        assert "query" in defn.parameters_schema.get("properties", {})


# -- RecallMemoryTool -------------------------------------------------------


@pytest.mark.unit
class TestRecallMemoryTool:
    def test_is_base_tool(self) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert isinstance(tool, BaseTool)

    def test_name(self) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert tool.name == "recall_memory"

    def test_category_is_memory(self) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert tool.category == ToolCategory.MEMORY

    def test_has_description(self) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert tool.description
        assert "memory" in tool.description.lower()

    def test_has_parameters_schema(self) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        schema = tool.parameters_schema
        assert schema is not None
        assert schema["type"] == "object"
        assert "memory_id" in schema["properties"]
        assert "memory_id" in schema["required"]

    def test_schema_is_deep_copied(self) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        schema_a = tool.parameters_schema
        schema_b = tool.parameters_schema
        assert schema_a is not schema_b
        assert schema_a == schema_b

    async def test_execute_delegates_to_strategy(self) -> None:
        entry = _make_entry(content="recalled memory")
        backend = _make_backend((entry,))
        backend.get = AsyncMock(return_value=entry)
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        tool = RecallMemoryTool(strategy=strategy, agent_id="agent-1")

        result = await tool.execute(arguments={"memory_id": "mem-1"})

        assert isinstance(result, ToolExecutionResult)
        assert "recalled memory" in result.content
        assert not result.is_error

    async def test_execute_not_found(self) -> None:
        backend = _make_backend()
        backend.get = AsyncMock(return_value=None)
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        tool = RecallMemoryTool(strategy=strategy, agent_id="agent-1")

        result = await tool.execute(arguments={"memory_id": "nonexistent"})

        assert result.is_error
        assert "not found" in result.content.lower()

    @pytest.mark.parametrize("memory_id", ["", "   ", "\t\n"])
    async def test_execute_blank_id_returns_error(
        self,
        memory_id: str,
    ) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        result = await tool.execute(arguments={"memory_id": memory_id})

        assert result.is_error

    async def test_execute_oversized_id_returns_error(self) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        result = await tool.execute(
            arguments={"memory_id": "x" * 300},
        )

        assert result.is_error
        assert "maximum" in result.content.lower()

    async def test_execute_backend_error_returns_error_result(self) -> None:
        from synthorg.memory.errors import MemoryRetrievalError

        backend = _make_backend()
        backend.get = AsyncMock(
            side_effect=MemoryRetrievalError("timeout"),
        )
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        tool = RecallMemoryTool(strategy=strategy, agent_id="agent-1")

        result = await tool.execute(arguments={"memory_id": "mem-1"})

        assert result.is_error
        assert "unavailable" in result.content.lower()
        assert "timeout" not in result.content

    def test_to_definition_produces_valid_tool_definition(self) -> None:
        tool = RecallMemoryTool(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        defn = tool.to_definition()
        assert defn.name == "recall_memory"
        assert defn.parameters_schema
        assert "memory_id" in defn.parameters_schema.get("properties", {})


# -- create_memory_tools factory --------------------------------------------


@pytest.mark.unit
class TestCreateMemoryTools:
    def test_returns_two_tools(self) -> None:
        tools = create_memory_tools(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        assert len(tools) == 2

    def test_tool_names(self) -> None:
        tools = create_memory_tools(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        names = {t.name for t in tools}
        assert names == {"search_memory", "recall_memory"}

    def test_all_are_base_tool_instances(self) -> None:
        tools = create_memory_tools(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        for tool in tools:
            assert isinstance(tool, BaseTool)

    def test_all_have_memory_category(self) -> None:
        tools = create_memory_tools(
            strategy=_make_strategy(),
            agent_id="agent-1",
        )
        for tool in tools:
            assert tool.category == ToolCategory.MEMORY

    async def test_agent_id_bound_to_tools(self) -> None:
        """Tools use the agent_id they were created with."""
        entry = _make_entry(content="agent-specific")
        backend = _make_backend((entry,))
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        tools = create_memory_tools(strategy=strategy, agent_id="agent-42")
        search = next(t for t in tools if t.name == "search_memory")

        await search.execute(arguments={"query": "test"})

        # Verify the backend was called with the bound agent_id
        call_args = backend.retrieve.call_args
        assert call_args.args[0] == "agent-42"


# -- registry_with_memory_tools ---------------------------------------------


@pytest.mark.unit
class TestRegistryWithMemoryTools:
    def test_adds_memory_tools_to_registry(self) -> None:
        base_registry = _make_empty_registry()
        strategy = _make_strategy()

        augmented = registry_with_memory_tools(
            base_registry,
            strategy,
            agent_id="agent-1",
        )

        assert "search_memory" in augmented
        assert "recall_memory" in augmented

    def test_preserves_existing_tools(self) -> None:
        base_registry = ToolRegistry([_DummyTool("existing_tool")])
        strategy = _make_strategy()

        augmented = registry_with_memory_tools(
            base_registry,
            strategy,
            agent_id="agent-1",
        )

        assert "existing_tool" in augmented
        assert "search_memory" in augmented
        assert "recall_memory" in augmented
        assert len(augmented) == 3

    def test_non_tool_strategy_returns_original_registry(self) -> None:
        """Non-ToolBasedInjectionStrategy returns registry unchanged."""
        base_registry = ToolRegistry([_DummyTool()])

        # Use a mock that satisfies MemoryInjectionStrategy but is not
        # ToolBasedInjectionStrategy
        mock_strategy = AsyncMock(spec=MemoryInjectionStrategy)
        mock_strategy.strategy_name = "context_injection"

        result = registry_with_memory_tools(
            base_registry,
            mock_strategy,
            agent_id="agent-1",
        )

        assert result is base_registry

    def test_none_strategy_returns_original_registry(self) -> None:
        base_registry = ToolRegistry([_DummyTool()])

        result = registry_with_memory_tools(
            base_registry,
            None,
            agent_id="agent-1",
        )

        assert result is base_registry

    async def test_full_round_trip_search(self) -> None:
        """Registry -> get tool -> execute -> get results."""
        entry = _make_entry(content="round-trip result")
        strategy = _make_strategy((entry,))
        base_registry = _make_empty_registry()

        augmented = registry_with_memory_tools(
            base_registry,
            strategy,
            agent_id="agent-1",
        )

        tool = augmented.get("search_memory")
        result = await tool.execute(arguments={"query": "test"})

        assert isinstance(result, ToolExecutionResult)
        assert "round-trip result" in result.content

    async def test_full_round_trip_recall(self) -> None:
        """Registry -> get tool -> execute -> get result by ID."""
        entry = _make_entry(content="recalled via registry")
        backend = _make_backend((entry,))
        backend.get = AsyncMock(return_value=entry)
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        base_registry = _make_empty_registry()

        augmented = registry_with_memory_tools(
            base_registry,
            strategy,
            agent_id="agent-1",
        )

        tool = augmented.get("recall_memory")
        result = await tool.execute(arguments={"memory_id": "mem-1"})

        assert isinstance(result, ToolExecutionResult)
        assert "recalled via registry" in result.content

    def test_to_definitions_includes_memory_tools(self) -> None:
        """Registry.to_definitions() includes memory tool schemas."""
        strategy = _make_strategy()
        augmented = registry_with_memory_tools(
            _make_empty_registry(),
            strategy,
            agent_id="agent-1",
        )

        defs = augmented.to_definitions()
        names = {d.name for d in defs}
        assert "search_memory" in names
        assert "recall_memory" in names

    def test_duplicate_names_raise_value_error(self) -> None:
        """Duplicate tool names are a config error and must propagate."""
        strategy = _make_strategy()
        augmented = registry_with_memory_tools(
            _make_empty_registry(),
            strategy,
            agent_id="agent-1",
        )
        # Second augmentation with same tools -- duplicate names
        with pytest.raises(ValueError, match="Duplicate tool name"):
            registry_with_memory_tools(
                augmented,
                strategy,
                agent_id="agent-1",
            )


# -- _is_error_response direct tests ----------------------------------------


@pytest.mark.unit
class TestIsErrorResponse:
    def test_error_prefix_matches(self) -> None:
        assert _is_error_response("Error: query must be a non-empty string.")

    def test_search_unavailable_matches(self) -> None:
        assert _is_error_response(
            "Error: Memory search is temporarily unavailable.",
        )

    def test_search_unexpected_matches(self) -> None:
        assert _is_error_response(
            "Error: Memory search encountered an unexpected error.",
        )

    def test_recall_unavailable_matches(self) -> None:
        assert _is_error_response(
            "Error: Memory recall is temporarily unavailable.",
        )

    def test_recall_unexpected_matches(self) -> None:
        assert _is_error_response(
            "Error: Memory recall encountered an unexpected error.",
        )

    def test_not_found_prefix_matches(self) -> None:
        assert _is_error_response("Error: Memory not found: mem-123")

    def test_no_memories_found_is_not_error(self) -> None:
        """Successful empty result must NOT be classified as error."""
        assert not _is_error_response("No memories found.")

    def test_normal_result_is_not_error(self) -> None:
        assert not _is_error_response("[episodic] (relevance: 0.85) test")

    def test_empty_string_is_not_error(self) -> None:
        assert not _is_error_response("")

    def test_partial_match_not_error(self) -> None:
        """'Error occurred' should NOT match -- prefix is 'Error:' with colon."""
        assert not _is_error_response("Error occurred somewhere")


# -- Generic exception path -------------------------------------------------


@pytest.mark.unit
class TestGenericExceptionPath:
    async def test_search_generic_exception_returns_error(self) -> None:
        backend = _make_backend()
        backend.retrieve = AsyncMock(side_effect=RuntimeError("internal boom"))
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        tool = SearchMemoryTool(strategy=strategy, agent_id="agent-1")

        result = await tool.execute(arguments={"query": "test"})

        assert result.is_error
        assert "unexpected error" in result.content.lower()
        # Must not leak internal error details
        assert "internal boom" not in result.content

    async def test_recall_generic_exception_returns_error(self) -> None:
        backend = _make_backend()
        backend.get = AsyncMock(side_effect=ConnectionError("refused"))
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        tool = RecallMemoryTool(strategy=strategy, agent_id="agent-1")

        result = await tool.execute(arguments={"memory_id": "mem-1"})

        assert result.is_error
        assert "unexpected error" in result.content.lower()
        assert "refused" not in result.content
