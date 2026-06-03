# mypy: disable-error-code="explicit-any"
"""Tests for SelfEditingMemoryStrategy core -- config, init, protocol, dispatch."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from typeguard import suppress_type_checks

from synthorg.core.enums import MemoryCategory
from synthorg.memory.injection import InjectionStrategy
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.self_editing import (
    ARCHIVAL_MEMORY_SEARCH_TOOL,
    ARCHIVAL_MEMORY_WRITE_TOOL,
    CORE_MEMORY_READ_TOOL,
    CORE_MEMORY_WRITE_TOOL,
    RECALL_MEMORY_READ_TOOL,
    RECALL_MEMORY_WRITE_TOOL,
    SelfEditingMemoryConfig,
    SelfEditingMemoryStrategy,
)
from synthorg.providers.enums import MessageRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    *,
    entry_id: str = "mem-1",
    agent_id: str = "agent-1",
    content: str = "test memory content",
    category: MemoryCategory = MemoryCategory.SEMANTIC,
    tags: tuple[str, ...] = ("core",),
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=MemoryMetadata(tags=tags),
        created_at=datetime.now(UTC),
    )


def _make_backend(
    *,
    entries: tuple[MemoryEntry, ...] = (),
    store_return: str = "new-mem-id",
) -> AsyncMock:
    backend = AsyncMock(spec=MemoryBackend)
    backend.retrieve = AsyncMock(return_value=entries)
    backend.get = AsyncMock(return_value=entries[0] if entries else None)
    backend.store = AsyncMock(return_value=store_return)
    return backend


def _make_config(**kwargs: Any) -> SelfEditingMemoryConfig:
    return SelfEditingMemoryConfig(**kwargs)


def _make_strategy(
    *,
    backend: AsyncMock | None = None,
    config: SelfEditingMemoryConfig | None = None,
    entries: tuple[MemoryEntry, ...] = (),
) -> SelfEditingMemoryStrategy:
    if backend is None:
        backend = _make_backend(entries=entries)
    if config is None:
        config = _make_config()
    return SelfEditingMemoryStrategy(backend=backend, config=config)


# ---------------------------------------------------------------------------
# TestSelfEditingMemoryConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelfEditingMemoryConfig:
    def test_default_values(self) -> None:
        config = SelfEditingMemoryConfig()

        assert config.core_memory_token_budget == 1024
        assert config.core_memory_tag == "core"
        assert config.allow_core_writes is True
        assert config.core_max_entries == 20
        assert config.archival_search_limit == 10
        assert MemoryCategory.WORKING not in config.archival_categories
        assert MemoryCategory.EPISODIC in config.archival_categories
        assert MemoryCategory.SEMANTIC in config.archival_categories
        assert MemoryCategory.PROCEDURAL in config.archival_categories
        assert MemoryCategory.SOCIAL in config.archival_categories
        assert config.write_auto_tag is True

    @pytest.mark.parametrize(
        ("budget", "valid"),
        [
            (256, True),
            (1024, True),
            (8192, True),
            (255, False),
            (8193, False),
        ],
    )
    def test_core_token_budget_bounds(self, budget: int, valid: bool) -> None:
        if valid:
            config = SelfEditingMemoryConfig(core_memory_token_budget=budget)
            assert config.core_memory_token_budget == budget
        else:
            with pytest.raises(ValidationError):
                SelfEditingMemoryConfig(core_memory_token_budget=budget)

    def test_working_rejected_in_archival_categories(self) -> None:
        """WORKING category must not be accepted in archival_categories."""
        with pytest.raises(ValidationError):
            SelfEditingMemoryConfig(
                archival_categories=frozenset({MemoryCategory.WORKING}),
            )

    def test_working_mixed_with_valid_categories_still_rejected(self) -> None:
        """Even one WORKING entry in an otherwise valid set must be rejected."""
        with pytest.raises(ValidationError):
            SelfEditingMemoryConfig(
                archival_categories=frozenset(
                    {
                        MemoryCategory.WORKING,
                        MemoryCategory.EPISODIC,
                    }
                ),
            )

    def test_frozen_prevents_mutation(self) -> None:
        config = SelfEditingMemoryConfig()
        with pytest.raises(ValidationError):
            config.allow_core_writes = False  # type: ignore[misc]

    def test_allow_core_writes_flag(self) -> None:
        enabled = SelfEditingMemoryConfig(allow_core_writes=True)
        disabled = SelfEditingMemoryConfig(allow_core_writes=False)

        assert enabled.allow_core_writes is True
        assert disabled.allow_core_writes is False

    def test_empty_archival_categories_rejected(self) -> None:
        """Empty archival_categories must be rejected.

        An empty set blocks all archival writes.
        """
        with pytest.raises(ValidationError):
            SelfEditingMemoryConfig(archival_categories=frozenset())


# ---------------------------------------------------------------------------
# TestSelfEditingMemoryStrategyInit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelfEditingMemoryStrategyInit:
    def test_requires_durable_backend(self) -> None:
        """Constructor must reject backend=None."""
        with suppress_type_checks(), pytest.raises(TypeError):
            SelfEditingMemoryStrategy(
                backend=None,  # type: ignore[arg-type]
                config=SelfEditingMemoryConfig(),
            )

    def test_strategy_name_is_self_editing(self) -> None:
        strategy = _make_strategy()

        assert strategy.strategy_name == "self_editing"
        assert strategy.strategy_name == InjectionStrategy.SELF_EDITING.value


# ---------------------------------------------------------------------------
# TestSelfEditingMemoryStrategyPrepareMessages
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelfEditingMemoryStrategyPrepareMessages:
    async def test_returns_system_message_with_core_entries(self) -> None:
        entry = _make_entry(
            content="I am a customer support agent",
            category=MemoryCategory.SEMANTIC,
            tags=("core",),
        )
        backend = _make_backend(entries=(entry,))
        strategy = _make_strategy(backend=backend)

        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="current task",
            token_budget=2048,
        )

        # Two messages: directive SYSTEM message + core memory message.
        assert len(messages) == 2
        directive_message, memory_message = messages
        assert directive_message.role is MessageRole.SYSTEM
        memory_content = memory_message.content
        assert memory_content is not None
        assert "customer support agent" in memory_content

    async def test_respects_token_budget(self) -> None:
        """token_budget=0 must return an empty tuple."""
        entry = _make_entry(content="x" * 500)
        backend = _make_backend(entries=(entry,))
        strategy = _make_strategy(backend=backend)

        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="task",
            token_budget=0,
        )

        assert messages == ()

    async def test_empty_core_memory_returns_empty_tuple(self) -> None:
        backend = _make_backend(entries=())
        strategy = _make_strategy(backend=backend)

        messages = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="anything",
            token_budget=1024,
        )

        assert messages == ()

    async def test_only_fetches_semantic_core_tagged_entries(self) -> None:
        """retrieve() must use text=None, SEMANTIC category, core tag."""
        backend = _make_backend(entries=())
        config = _make_config(core_memory_tag="core")
        strategy = _make_strategy(backend=backend, config=config)

        await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="anything",
            token_budget=1024,
        )

        call_args = backend.retrieve.call_args
        query = call_args.args[1]
        assert query.text is None
        assert MemoryCategory.SEMANTIC in (query.categories or frozenset())
        assert "core" in query.tags

    async def test_backend_error_returns_empty_not_raise(self) -> None:
        """Non-system backend errors in prepare_messages must return ()."""
        backend = _make_backend()
        backend.retrieve = AsyncMock(side_effect=RuntimeError("backend down"))
        strategy = _make_strategy(backend=backend)

        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="anything",
            token_budget=1024,
        )

        assert result == ()


# ---------------------------------------------------------------------------
# TestSelfEditingMemoryStrategyToolDefinitions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelfEditingMemoryStrategyToolDefinitions:
    def test_returns_six_definitions(self) -> None:
        strategy = _make_strategy()

        assert len(strategy.get_tool_definitions()) == 6

    def test_tool_names_match_constants(self) -> None:
        strategy = _make_strategy()
        names = {d.name for d in strategy.get_tool_definitions()}

        assert names == {
            CORE_MEMORY_READ_TOOL,
            CORE_MEMORY_WRITE_TOOL,
            ARCHIVAL_MEMORY_SEARCH_TOOL,
            ARCHIVAL_MEMORY_WRITE_TOOL,
            RECALL_MEMORY_READ_TOOL,
            RECALL_MEMORY_WRITE_TOOL,
        }

    def test_all_definitions_have_schemas(self) -> None:
        strategy = _make_strategy()

        for defn in strategy.get_tool_definitions():
            assert defn.parameters_schema is not None, f"{defn.name} missing schema"
            assert "type" in defn.parameters_schema


# ---------------------------------------------------------------------------
# TestHandleToolCallDispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleToolCallDispatch:
    async def test_unknown_tool_name_returns_error_string(self) -> None:
        """Unknown tool names must return an error string, not raise."""
        strategy = _make_strategy()

        result = await strategy.handle_tool_call("no_such_tool", {}, "agent-1")

        assert result.startswith("Error:")

    async def test_memory_error_is_reraised(self) -> None:
        """MemoryError from a handler must propagate through handle_tool_call."""
        backend = _make_backend()
        backend.retrieve = AsyncMock(side_effect=MemoryError("out of memory"))
        strategy = _make_strategy(backend=backend)

        with pytest.raises(MemoryError):
            await strategy.handle_tool_call(CORE_MEMORY_READ_TOOL, {}, "agent-1")

    async def test_recursion_error_is_reraised(self) -> None:
        """RecursionError from a handler must propagate."""
        backend = _make_backend()
        backend.retrieve = AsyncMock(side_effect=RecursionError("max recursion"))
        strategy = _make_strategy(backend=backend)

        with pytest.raises(RecursionError):
            await strategy.handle_tool_call(CORE_MEMORY_READ_TOOL, {}, "agent-1")

    async def test_generic_exception_returns_error_prefix(self) -> None:
        """Generic exceptions from handlers must be caught and returned."""
        backend = _make_backend()
        backend.retrieve = AsyncMock(side_effect=RuntimeError("backend exploded"))
        strategy = _make_strategy(backend=backend)

        result = await strategy.handle_tool_call(CORE_MEMORY_READ_TOOL, {}, "agent-1")

        assert result.startswith("Error:")
        assert "Memory operation failed." in result

    async def test_generic_exception_does_not_expose_backend_details(self) -> None:
        """Error message must not leak the original exception text."""
        backend = _make_backend()
        backend.retrieve = AsyncMock(
            side_effect=RuntimeError("SQLite UNIQUE constraint failed: entries.id")
        )
        strategy = _make_strategy(backend=backend)

        result = await strategy.handle_tool_call(CORE_MEMORY_READ_TOOL, {}, "agent-1")

        assert "SQLite" not in result
        assert "UNIQUE constraint" not in result


# ``_extract_str`` is enforced via the ``NotBlankStr`` field type in
# ``self_editing_args.py`` and exercised by
# ``tests/unit/memory/test_self_editing_args.py``.
