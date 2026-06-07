"""Tests for ToolBasedInjectionStrategy."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.injection import InjectionStrategy, MemoryInjectionStrategy
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.reformulation import QueryReformulator, SufficiencyChecker
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
from synthorg.memory.tool_retriever_helpers import merge_results


def _make_entry(
    *,
    entry_id: str = "mem-1",
    agent_id: str = "agent-1",
    content: str = "test memory",
    relevance_score: float | None = 0.8,
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


# -- Protocol compliance ---------------------------------------------------


@pytest.mark.unit
class TestToolBasedProtocol:
    def test_satisfies_protocol(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        assert isinstance(strategy, MemoryInjectionStrategy)

    def test_strategy_name(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        assert strategy.strategy_name == "tool_based"


# -- prepare_messages -------------------------------------------------------


@pytest.mark.unit
class TestPrepareMessages:
    async def test_returns_instruction_message(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert len(result) == 1
        assert result[0].content is not None
        assert "memory" in result[0].content.lower()

    async def test_zero_budget_returns_empty(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=0,
        )
        assert result == ()


# -- get_tool_definitions ---------------------------------------------------


@pytest.mark.unit
class TestToolDefinitions:
    def test_returns_two_tools(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        tools = strategy.get_tool_definitions()
        assert len(tools) == 2

    def test_tool_names(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        tools = strategy.get_tool_definitions()
        names = {t.name for t in tools}
        assert "search_memory" in names
        assert "recall_memory" in names

    def test_tools_have_schemas(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        tools = strategy.get_tool_definitions()
        for tool in tools:
            assert tool.parameters_schema
            assert "type" in tool.parameters_schema

    def test_search_memory_schema(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        tools = strategy.get_tool_definitions()
        search = next(t for t in tools if t.name == "search_memory")
        props = cast("dict[str, Any]", search.parameters_schema.get("properties", {}))
        assert "query" in props
        assert "limit" in props

    def test_recall_memory_schema(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        tools = strategy.get_tool_definitions()
        recall = next(t for t in tools if t.name == "recall_memory")
        props = cast("dict[str, Any]", recall.parameters_schema.get("properties", {}))
        assert "memory_id" in props


# -- handle_tool_call -------------------------------------------------------


@pytest.mark.unit
class TestHandleToolCall:
    async def test_search_memory_returns_results(self) -> None:
        entry = _make_entry(content="found memory")
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend((entry,)),
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "test search"},
            agent_id="agent-1",
        )
        assert "found memory" in result

    async def test_recall_memory_returns_entry(self) -> None:
        entry = _make_entry(content="recalled memory")
        backend = _make_backend((entry,))
        backend.get = AsyncMock(return_value=entry)
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="recall_memory",
            arguments={"memory_id": "mem-1"},
            agent_id="agent-1",
        )
        assert "recalled memory" in result

    async def test_recall_memory_not_found(self) -> None:
        backend = _make_backend()
        backend.get = AsyncMock(return_value=None)
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="recall_memory",
            arguments={"memory_id": "nonexistent"},
            agent_id="agent-1",
        )
        assert "not found" in result.lower()

    async def test_unknown_tool_raises(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        with pytest.raises(ValueError, match="Unknown tool"):
            await strategy.handle_tool_call(
                tool_name="unknown_tool",
                arguments={},
                agent_id="agent-1",
            )

    async def test_search_with_limit(self) -> None:
        entry = _make_entry(content="limited result")
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend((entry,)),
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "test", "limit": 5},
            agent_id="agent-1",
        )
        assert isinstance(result, str)

    async def test_search_empty_results(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "nothing here"},
            agent_id="agent-1",
        )
        assert "no memories found" in result.lower()

    async def test_search_error_returns_generic_message(self) -> None:
        from synthorg.memory.errors import MemoryRetrievalError

        backend = _make_backend()
        backend.retrieve = AsyncMock(
            side_effect=MemoryRetrievalError("db down"),
        )
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "will fail"},
            agent_id="agent-1",
        )
        assert "unavailable" in result.lower()
        # Must NOT leak internal error details
        assert "db down" not in result

    async def test_search_empty_query_returns_error(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": ""},
            agent_id="agent-1",
        )
        assert "non-empty" in result.lower()

    async def test_search_with_categories(self) -> None:
        entry = _make_entry(content="categorized memory")
        backend = _make_backend((entry,))
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={
                "query": "test",
                "categories": ["episodic", "semantic"],
            },
            agent_id="agent-1",
        )
        assert isinstance(result, str)
        # Verify categories were passed to the query
        call_args = backend.retrieve.call_args
        query = call_args[0][1]
        assert query.categories is not None
        assert MemoryCategory.EPISODIC in query.categories

    async def test_recall_empty_memory_id_returns_error(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend(),
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="recall_memory",
            arguments={"memory_id": ""},
            agent_id="agent-1",
        )
        assert "memory_id is required" in result.lower()

    async def test_search_system_error_propagates(self) -> None:
        backend = _make_backend()
        # builtins.MemoryError (not synthorg domain MemoryError)
        backend.retrieve = AsyncMock(side_effect=MemoryError)
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        with pytest.raises(MemoryError):
            await strategy.handle_tool_call(
                tool_name="search_memory",
                arguments={"query": "test"},
                agent_id="agent-1",
            )

    async def test_recall_system_error_propagates(self) -> None:
        backend = _make_backend()
        backend.get = AsyncMock(side_effect=RecursionError)
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),
        )
        with pytest.raises(RecursionError):
            await strategy.handle_tool_call(
                tool_name="recall_memory",
                arguments={"memory_id": "mem-1"},
                agent_id="agent-1",
            )


# -- Query reformulation (Search-and-Ask) ----------------------------------


def _reformulation_config(max_rounds: int = 2) -> MemoryRetrievalConfig:
    """Config with query reformulation enabled."""
    return MemoryRetrievalConfig(
        strategy=InjectionStrategy.TOOL_BASED,
        min_relevance=0.0,
        query_reformulation_enabled=True,
        max_reformulation_rounds=max_rounds,
    )


@pytest.mark.unit
class TestSearchWithReformulation:
    async def test_disabled_is_single_shot(self) -> None:
        """Default config does not reformulate."""
        entries = (_make_entry(entry_id="a"),)
        backend = _make_backend(entries)

        # Use sufficiency checker that would return False
        sufficiency = AsyncMock(spec=SufficiencyChecker)
        sufficiency.check_sufficiency = AsyncMock(return_value=False)
        reformulator = AsyncMock(spec=QueryReformulator)
        reformulator.reformulate = AsyncMock(return_value="new query")

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_tool_config(),  # reformulation disabled by default
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )

        await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "find me memories"},
            agent_id="agent-1",
        )

        # Only one retrieve call -- no reformulation
        assert backend.retrieve.call_count == 1
        sufficiency.check_sufficiency.assert_not_called()
        reformulator.reformulate.assert_not_called()

    async def test_missing_reformulator_raises(self) -> None:
        """ToolBasedInjectionStrategy fails fast when the reformulation
        config flag is set but ``reformulator`` is missing -- the silent
        no-op fallback is a configuration trap.
        """
        entries = (_make_entry(entry_id="a"),)
        backend = _make_backend(entries)
        with pytest.raises(
            ValueError,
            match=r"reformulator and sufficiency_checker must both be provided",
        ):
            ToolBasedInjectionStrategy(
                backend=backend,
                config=_reformulation_config(),
                reformulator=None,
                sufficiency_checker=AsyncMock(spec=SufficiencyChecker),
            )

    async def test_missing_sufficiency_checker_raises(self) -> None:
        """Symmetric: a missing sufficiency_checker is also a hard error."""
        entries = (_make_entry(entry_id="a"),)
        backend = _make_backend(entries)
        with pytest.raises(
            ValueError,
            match=r"reformulator and sufficiency_checker must both be provided",
        ):
            ToolBasedInjectionStrategy(
                backend=backend,
                config=_reformulation_config(),
                reformulator=AsyncMock(spec=QueryReformulator),
                sufficiency_checker=None,
            )

    async def test_sufficient_on_first_try_no_reformulation(self) -> None:
        """When results are sufficient, no reformulation is performed."""
        entries = (_make_entry(entry_id="a"),)
        backend = _make_backend(entries)

        sufficiency = AsyncMock(spec=SufficiencyChecker)
        sufficiency.check_sufficiency = AsyncMock(return_value=True)
        reformulator = AsyncMock(spec=QueryReformulator)
        reformulator.reformulate = AsyncMock(return_value="unused")

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_reformulation_config(),
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )

        await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "find me memories"},
            agent_id="agent-1",
        )

        sufficiency.check_sufficiency.assert_called_once()
        reformulator.reformulate.assert_not_called()
        # Only one retrieve
        assert backend.retrieve.call_count == 1

    async def test_insufficient_then_sufficient_one_round(self) -> None:
        """Insufficient -> reformulate -> sufficient: one round."""
        initial = (_make_entry(entry_id="a", content="initial"),)
        refined = (_make_entry(entry_id="b", content="refined"),)

        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(side_effect=[initial, refined])

        sufficiency = AsyncMock(spec=SufficiencyChecker)
        sufficiency.check_sufficiency = AsyncMock(side_effect=[False, True])
        reformulator = AsyncMock(spec=QueryReformulator)
        reformulator.reformulate = AsyncMock(return_value="refined query")

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_reformulation_config(max_rounds=3),
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )

        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "original query"},
            agent_id="agent-1",
        )

        assert backend.retrieve.call_count == 2
        reformulator.reformulate.assert_called_once()
        # Result should include both initial and refined entries
        assert "initial" in result
        assert "refined" in result

    async def test_max_rounds_reached(self) -> None:
        """Stops after max_reformulation_rounds."""
        entries = (_make_entry(entry_id="a"),)
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=entries)

        sufficiency = AsyncMock(spec=SufficiencyChecker)
        # Always insufficient
        sufficiency.check_sufficiency = AsyncMock(return_value=False)
        reformulator = AsyncMock(spec=QueryReformulator)
        # Return a new query each round (alternating)
        reformulator.reformulate = AsyncMock(
            side_effect=["query1", "query2", "query3", "query4"],
        )

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_reformulation_config(max_rounds=2),
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )

        await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "original"},
            agent_id="agent-1",
        )

        # Initial retrieve + 2 rounds = 3 retrieves
        assert backend.retrieve.call_count == 3
        # 2 sufficiency checks (one per round) + 1 final check = 3
        assert sufficiency.check_sufficiency.call_count == 3
        # 2 reformulations
        assert reformulator.reformulate.call_count == 2

    async def test_reformulator_returns_none_stops_loop(self) -> None:
        """Returns current results when reformulator returns None."""
        entries = (_make_entry(entry_id="a"),)
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=entries)

        sufficiency = AsyncMock(spec=SufficiencyChecker)
        sufficiency.check_sufficiency = AsyncMock(return_value=False)
        reformulator = AsyncMock(spec=QueryReformulator)
        reformulator.reformulate = AsyncMock(return_value=None)

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_reformulation_config(max_rounds=3),
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )

        await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "original"},
            agent_id="agent-1",
        )

        # Initial retrieve only -- no further rounds after None
        assert backend.retrieve.call_count == 1
        reformulator.reformulate.assert_called_once()

    async def test_reformulator_returns_same_query_stops(self) -> None:
        """Loop stops when reformulator returns the same query."""
        entries = (_make_entry(entry_id="a"),)
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=entries)

        sufficiency = AsyncMock(spec=SufficiencyChecker)
        sufficiency.check_sufficiency = AsyncMock(return_value=False)
        reformulator = AsyncMock(spec=QueryReformulator)
        reformulator.reformulate = AsyncMock(return_value="original")

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_reformulation_config(max_rounds=3),
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )

        await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "original"},
            agent_id="agent-1",
        )

        # Should stop after one reformulation attempt
        assert backend.retrieve.call_count == 1

    async def test_reformulation_dedupes_results_across_rounds(self) -> None:
        """Entries with the same ID across rounds are merged, not duplicated."""
        shared = _make_entry(
            entry_id="shared",
            content="shared content",
            relevance_score=0.5,
        )
        new_entry = _make_entry(entry_id="new-1", content="new content")

        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(side_effect=[(shared,), (shared, new_entry)])

        sufficiency = AsyncMock(spec=SufficiencyChecker)
        sufficiency.check_sufficiency = AsyncMock(side_effect=[False, True])
        reformulator = AsyncMock(spec=QueryReformulator)
        reformulator.reformulate = AsyncMock(return_value="refined")

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_reformulation_config(max_rounds=2),
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )

        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "start"},
            agent_id="agent-1",
        )
        # "shared" appears exactly once in the formatted output.
        assert result.count("shared content") == 1
        assert "new content" in result

    async def test_sufficiency_checker_error_returns_current_entries(
        self,
    ) -> None:
        """A sufficiency checker exception degrades to current entries."""
        entries = (_make_entry(entry_id="a", content="initial"),)
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=entries)

        sufficiency = AsyncMock(spec=SufficiencyChecker)
        sufficiency.check_sufficiency = AsyncMock(
            side_effect=RuntimeError("checker down"),
        )
        reformulator = AsyncMock(spec=QueryReformulator)

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_reformulation_config(max_rounds=2),
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )

        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "test"},
            agent_id="agent-1",
        )
        # Initial entries should still be returned, no error leaked.
        assert "initial" in result
        assert "checker down" not in result
        # Reformulator never reached.
        reformulator.reformulate.assert_not_called()

    async def test_reformulator_error_returns_current_entries(self) -> None:
        """A reformulator exception degrades to current entries."""
        entries = (_make_entry(entry_id="a", content="initial"),)
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=entries)

        sufficiency = AsyncMock(spec=SufficiencyChecker)
        sufficiency.check_sufficiency = AsyncMock(return_value=False)
        reformulator = AsyncMock(spec=QueryReformulator)
        reformulator.reformulate = AsyncMock(
            side_effect=RuntimeError("reformulator down"),
        )

        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_reformulation_config(max_rounds=2),
            reformulator=reformulator,
            sufficiency_checker=sufficiency,
        )
        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={"query": "test"},
            agent_id="agent-1",
        )
        assert "initial" in result
        assert "reformulator down" not in result


@pytest.mark.unit
class TestInvalidCategoryHandling:
    async def test_invalid_categories_surface_to_llm(self) -> None:
        """Invalid category values appear in the response for LLM correction."""
        entry = _make_entry(content="memory")
        strategy = ToolBasedInjectionStrategy(
            backend=_make_backend((entry,)),
            config=_tool_config(),
        )
        result = await strategy.handle_tool_call(
            tool_name="search_memory",
            arguments={
                "query": "test",
                "categories": ["episodic", "long_term", "foo"],
            },
            agent_id="agent-1",
        )
        assert "long_term" in result
        assert "foo" in result
        assert "Ignored invalid categories" in result


@pytest.mark.unit
class TestMergeResults:
    def test_empty_inputs(self) -> None:
        assert merge_results((), ()) == ()

    def test_disjoint_entries_concatenated(self) -> None:
        a = _make_entry(entry_id="a")
        b = _make_entry(entry_id="b")
        result = merge_results((a,), (b,))
        assert len(result) == 2
        ids = [e.id for e in result]
        assert ids == ["a", "b"]

    @pytest.mark.parametrize(
        ("existing_rel", "new_rel", "expected_rel"),
        [
            # Higher relevance wins regardless of which side it came from.
            pytest.param(0.3, 0.9, 0.9, id="new_wins"),
            pytest.param(0.9, 0.3, 0.9, id="existing_wins"),
            # None on either side is treated as 0.0.
            pytest.param(None, 0.1, 0.1, id="none_existing_vs_scored_new"),
            pytest.param(0.1, None, 0.1, id="scored_existing_vs_none_new"),
            # Exact tie keeps the existing entry (first-seen wins).
            pytest.param(0.5, 0.5, 0.5, id="tie_keeps_existing"),
        ],
    )
    def test_collision_keeps_higher_relevance(
        self,
        existing_rel: float | None,
        new_rel: float | None,
        expected_rel: float,
    ) -> None:
        existing = _make_entry(entry_id="dup", relevance_score=existing_rel)
        new = _make_entry(entry_id="dup", relevance_score=new_rel)
        result = merge_results((existing,), (new,))
        assert len(result) == 1
        assert result[0].relevance_score == expected_rel

    def test_merge_reorders_by_relevance(self) -> None:
        """Merged output is sorted by relevance so later rounds can surface.

        Regression guard against the "first-round wins" merge policy
        that prevented Search-and-Ask from surfacing improved matches
        in reformulation rounds.
        """
        low_first = _make_entry(entry_id="low", relevance_score=0.2)
        high_later = _make_entry(entry_id="high", relevance_score=0.9)
        result = merge_results((low_first,), (high_later,))
        ids = [e.id for e in result]
        assert ids == ["high", "low"], (
            "merge must sort by relevance so later unseen high-relevance "
            f"entries displace earlier low-relevance ones; got {ids}"
        )
