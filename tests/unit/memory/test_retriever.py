"""Tests for ContextInjectionStrategy (retriever pipeline)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.core.memory_enums import MemoryCategory
from synthorg.engine.prompt_safety import TAG_MEMORY_ENTRY
from synthorg.memory.errors import MemoryRetrievalError
from synthorg.memory.filter import (
    NON_INFERABLE_TAG,
    TagBasedMemoryFilter,
)
from synthorg.memory.injection import (
    DefaultTokenEstimator,
    MemoryInjectionStrategy,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.ranking import ScoredMemory
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.providers.enums import MessageRole


def _make_entry(
    *,
    entry_id: str = "mem-1",
    agent_id: str = "agent-1",
    content: str = "test memory",
    category: MemoryCategory = MemoryCategory.EPISODIC,
    relevance_score: float | None = 0.8,
) -> MemoryEntry:
    """Helper to build a MemoryEntry."""
    return MemoryEntry(
        id=entry_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=MemoryMetadata(),
        created_at=datetime.now(UTC),
        relevance_score=relevance_score,
    )


def _make_backend(entries: tuple[MemoryEntry, ...] = ()) -> AsyncMock:
    """Create a mock MemoryBackend (dense-only, no sparse support)."""
    backend = AsyncMock(spec=MemoryBackend)
    backend.retrieve = AsyncMock(return_value=entries)
    backend.supports_sparse_search = False
    return backend


def _make_shared_store(
    entries: tuple[MemoryEntry, ...] = (),
) -> AsyncMock:
    """Create a mock SharedKnowledgeStore."""
    store = AsyncMock(spec=SharedKnowledgeStore)
    store.search_shared = AsyncMock(return_value=entries)
    return store


# -- Protocol compliance ---------------------------------------------


@pytest.mark.unit
class TestContextInjectionStrategyProtocol:
    def test_satisfies_protocol(self) -> None:
        strategy = ContextInjectionStrategy(
            backend=_make_backend(),
            config=MemoryRetrievalConfig(),
        )
        assert isinstance(strategy, MemoryInjectionStrategy)

    def test_strategy_name(self) -> None:
        strategy = ContextInjectionStrategy(
            backend=_make_backend(),
            config=MemoryRetrievalConfig(),
        )
        assert strategy.strategy_name == "context_injection"

    def test_get_tool_definitions_empty(self) -> None:
        strategy = ContextInjectionStrategy(
            backend=_make_backend(),
            config=MemoryRetrievalConfig(),
        )
        assert strategy.get_tool_definitions() == ()


# -- prepare_messages -------------------------------------------------


@pytest.mark.unit
class TestPrepareMessages:
    async def test_returns_messages_with_memories(self) -> None:
        entry = _make_entry(content="important fact")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((entry,)),
            config=MemoryRetrievalConfig(min_relevance=0.0),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="what do I know",
            token_budget=1000,
        )
        # Two messages: directive SYSTEM message + memory message.
        assert len(result) == 2
        directive_message, memory_message = result
        assert directive_message.role is MessageRole.SYSTEM
        assert memory_message.role is MessageRole.SYSTEM
        memory_content = memory_message.content
        assert memory_content is not None
        assert "important fact" in memory_content
        assert f"<{TAG_MEMORY_ENTRY}>" in memory_content

    async def test_empty_backend_returns_empty(self) -> None:
        strategy = ContextInjectionStrategy(
            backend=_make_backend(()),
            config=MemoryRetrievalConfig(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert result == ()

    async def test_backend_called_with_correct_query(self) -> None:
        backend = _make_backend(())
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(max_memories=15),
        )
        await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="search text",
            token_budget=500,
        )
        backend.retrieve.assert_called_once()
        call_args = backend.retrieve.call_args
        assert call_args[0][0] == "agent-1"
        query: MemoryQuery = call_args[0][1]
        assert query.text == "search text"
        assert query.limit == 15

    async def test_categories_passed_through(self) -> None:
        backend = _make_backend(())
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(),
        )
        cats = frozenset({MemoryCategory.SEMANTIC, MemoryCategory.EPISODIC})
        await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=500,
            categories=cats,
        )
        query: MemoryQuery = backend.retrieve.call_args[0][1]
        assert query.categories == cats


@pytest.mark.unit
class TestSharedStoreMerge:
    async def test_shared_memories_included(self) -> None:
        personal = _make_entry(entry_id="p1", content="personal memory")
        shared = _make_entry(entry_id="s1", content="shared knowledge")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((personal,)),
            config=MemoryRetrievalConfig(
                include_shared=True,
                min_relevance=0.0,
            ),
            shared_store=_make_shared_store((shared,)),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "personal memory" in content
        assert "shared knowledge" in content

    async def test_include_shared_false_skips_shared(self) -> None:
        shared = _make_entry(entry_id="s1", content="shared data")
        shared_store = _make_shared_store((shared,))
        strategy = ContextInjectionStrategy(
            backend=_make_backend(()),
            config=MemoryRetrievalConfig(include_shared=False),
            shared_store=shared_store,
        )
        await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        shared_store.search_shared.assert_not_called()

    async def test_no_shared_store_still_works(self) -> None:
        entry = _make_entry(content="only personal")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((entry,)),
            config=MemoryRetrievalConfig(
                include_shared=True,
                min_relevance=0.0,
            ),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert len(result) == 2
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "only personal" in content


# -- Graceful degradation ---------------------------------------------


@pytest.mark.unit
class TestGracefulDegradation:
    async def test_backend_error_returns_empty(self) -> None:
        backend = _make_backend()
        backend.retrieve = AsyncMock(
            side_effect=MemoryRetrievalError("db down"),
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert result == ()

    async def test_shared_error_returns_personal_only(self) -> None:
        personal = _make_entry(content="personal survives")
        shared_store = _make_shared_store()
        shared_store.search_shared = AsyncMock(
            side_effect=MemoryRetrievalError("shared db down"),
        )
        strategy = ContextInjectionStrategy(
            backend=_make_backend((personal,)),
            config=MemoryRetrievalConfig(
                include_shared=True,
                min_relevance=0.0,
            ),
            shared_store=shared_store,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert len(result) == 2
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "personal survives" in content

    async def test_generic_exception_returns_empty(self) -> None:
        backend = _make_backend()
        backend.retrieve = AsyncMock(
            side_effect=RuntimeError("unexpected"),
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert result == ()

    async def test_builtin_memory_error_propagates(self) -> None:
        backend = _make_backend()
        backend.retrieve = AsyncMock(
            side_effect=MemoryError("out of memory"),
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(),
        )
        with pytest.raises(MemoryError, match="out of memory"):
            await strategy.prepare_messages(
                agent_id="agent-1",
                query_text="query",
                token_budget=1000,
            )

    async def test_recursion_error_propagates(self) -> None:
        backend = _make_backend()
        backend.retrieve = AsyncMock(side_effect=RecursionError())
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(),
        )
        with pytest.raises(RecursionError):
            await strategy.prepare_messages(
                agent_id="agent-1",
                query_text="query",
                token_budget=1000,
            )

    async def test_shared_builtin_memory_error_propagates(self) -> None:
        """builtins.MemoryError from shared store propagates (not swallowed)."""
        personal = _make_entry(content="personal data")
        shared_store = _make_shared_store()
        shared_store.search_shared = AsyncMock(
            side_effect=MemoryError("out of memory"),
        )
        strategy = ContextInjectionStrategy(
            backend=_make_backend((personal,)),
            config=MemoryRetrievalConfig(include_shared=True),
            shared_store=shared_store,
        )
        with pytest.raises(MemoryError, match="out of memory"):
            await strategy.prepare_messages(
                agent_id="agent-1",
                query_text="query",
                token_budget=1000,
            )

    async def test_shared_recursion_error_propagates(self) -> None:
        """RecursionError from shared store propagates (not swallowed)."""
        personal = _make_entry(content="personal data")
        shared_store = _make_shared_store()
        shared_store.search_shared = AsyncMock(
            side_effect=RecursionError(),
        )
        strategy = ContextInjectionStrategy(
            backend=_make_backend((personal,)),
            config=MemoryRetrievalConfig(include_shared=True),
            shared_store=shared_store,
        )
        with pytest.raises(RecursionError):
            await strategy.prepare_messages(
                agent_id="agent-1",
                query_text="query",
                token_budget=1000,
            )

    async def test_shared_generic_exception_returns_personal_only(self) -> None:
        """Generic exception from shared store degrades gracefully."""
        personal = _make_entry(content="personal survives generic")
        shared_store = _make_shared_store()
        shared_store.search_shared = AsyncMock(
            side_effect=RuntimeError("unexpected shared failure"),
        )
        strategy = ContextInjectionStrategy(
            backend=_make_backend((personal,)),
            config=MemoryRetrievalConfig(
                include_shared=True,
                min_relevance=0.0,
            ),
            shared_store=shared_store,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert len(result) == 2
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "personal survives generic" in content

    async def test_both_backends_failing_returns_empty(self) -> None:
        """When both personal and shared backends fail, returns empty."""
        backend = _make_backend()
        backend.retrieve = AsyncMock(
            side_effect=MemoryRetrievalError("personal db down"),
        )
        shared_store = _make_shared_store()
        shared_store.search_shared = AsyncMock(
            side_effect=MemoryRetrievalError("shared db down"),
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(include_shared=True),
            shared_store=shared_store,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert result == ()


# -- Token budget -----------------------------------------------------


@pytest.mark.unit
class TestTokenBudget:
    async def test_custom_token_estimator(self) -> None:
        entry = _make_entry(content="short")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((entry,)),
            config=MemoryRetrievalConfig(min_relevance=0.0),
            token_estimator=DefaultTokenEstimator(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=1000,
        )
        assert len(result) == 2

    async def test_zero_budget_returns_empty(self) -> None:
        entry = _make_entry(content="content")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((entry,)),
            config=MemoryRetrievalConfig(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=0,
        )
        assert result == ()


# -- Memory filter integration ----------------------------------------


@pytest.mark.unit
class TestMemoryFilterIntegration:
    async def test_filter_applied_after_ranking(self) -> None:
        """TagBasedMemoryFilter excludes untagged memories after ranking."""
        tagged = _make_entry(
            entry_id="tagged",
            content="tagged memory",
            relevance_score=0.9,
        )
        tagged = tagged.model_copy(
            update={
                "metadata": MemoryMetadata(tags=(NON_INFERABLE_TAG,)),
            },
        )
        untagged = _make_entry(
            entry_id="untagged",
            content="untagged memory",
            relevance_score=0.9,
        )
        strategy = ContextInjectionStrategy(
            backend=_make_backend((tagged, untagged)),
            config=MemoryRetrievalConfig(min_relevance=0.0),
            memory_filter=TagBasedMemoryFilter(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "tagged memory" in content
        assert "untagged memory" not in content

    async def test_filter_skipped_when_none(self) -> None:
        """When memory_filter is None, all ranked memories are injected."""
        entry = _make_entry(content="all memories pass")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((entry,)),
            config=MemoryRetrievalConfig(
                min_relevance=0.0,
                non_inferable_only=False,
            ),
            memory_filter=None,
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "all memories pass" in content

    async def test_filter_reduces_output(self) -> None:
        """Filter that excludes everything returns empty result."""
        entry = _make_entry(content="will be filtered out")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((entry,)),
            config=MemoryRetrievalConfig(min_relevance=0.0),
            memory_filter=TagBasedMemoryFilter(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert result == ()

    async def test_non_inferable_only_config_creates_filter(self) -> None:
        """non_inferable_only=True auto-creates TagBasedMemoryFilter."""
        tagged = _make_entry(
            entry_id="tagged",
            content="tagged memory",
            relevance_score=0.9,
        )
        tagged = tagged.model_copy(
            update={"metadata": MemoryMetadata(tags=(NON_INFERABLE_TAG,))},
        )
        untagged = _make_entry(
            entry_id="untagged",
            content="untagged memory",
            relevance_score=0.9,
        )
        strategy = ContextInjectionStrategy(
            backend=_make_backend((tagged, untagged)),
            config=MemoryRetrievalConfig(
                min_relevance=0.0,
                non_inferable_only=True,
            ),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert len(result) == 2
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "tagged memory" in content
        assert "untagged memory" not in content

    async def test_memory_filter_strategy_tag_based(self) -> None:
        """memory_filter_strategy='tag_based' retains only tagged memories."""
        tagged = _make_entry(
            entry_id="tagged", content="tagged memory", relevance_score=0.9
        ).model_copy(
            update={"metadata": MemoryMetadata(tags=(NON_INFERABLE_TAG,))},
        )
        untagged = _make_entry(
            entry_id="untagged", content="untagged memory", relevance_score=0.9
        )
        strategy = ContextInjectionStrategy(
            backend=_make_backend((tagged, untagged)),
            config=MemoryRetrievalConfig(
                min_relevance=0.0,
                memory_filter_strategy="tag_based",
            ),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "tagged memory" in content
        assert "untagged memory" not in content

    async def test_memory_filter_strategy_passthrough_injects_all(self) -> None:
        """memory_filter_strategy='passthrough' injects every memory."""
        tagged = _make_entry(
            entry_id="tagged", content="tagged memory", relevance_score=0.9
        ).model_copy(
            update={"metadata": MemoryMetadata(tags=(NON_INFERABLE_TAG,))},
        )
        untagged = _make_entry(
            entry_id="untagged", content="untagged memory", relevance_score=0.9
        )
        strategy = ContextInjectionStrategy(
            backend=_make_backend((tagged, untagged)),
            config=MemoryRetrievalConfig(
                min_relevance=0.0,
                memory_filter_strategy="passthrough",
            ),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        _, memory_message = result
        content = memory_message.content
        assert content is not None
        assert "tagged memory" in content
        assert "untagged memory" in content

    async def test_filter_failure_fails_closed(self) -> None:
        """Filter error returns no messages (fail closed, never leak)."""

        class _BrokenFilter:
            def filter_for_injection(
                self,
                memories: tuple[ScoredMemory, ...],
            ) -> tuple[ScoredMemory, ...]:
                msg = "filter exploded"
                raise RuntimeError(msg)

            @property
            def strategy_name(self) -> str:
                return "broken"

        entry = _make_entry(content="should not leak")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((entry,)),
            config=MemoryRetrievalConfig(min_relevance=0.0),
            memory_filter=_BrokenFilter(),
        )
        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="query",
            token_budget=5000,
        )
        assert result == ()

    async def test_filter_memory_error_propagates(self) -> None:
        """MemoryError through the filter path is re-raised."""

        class _MemoryErrorFilter:
            def filter_for_injection(
                self,
                memories: tuple[ScoredMemory, ...],
            ) -> tuple[ScoredMemory, ...]:
                raise MemoryError

            @property
            def strategy_name(self) -> str:
                return "oom"

        entry = _make_entry(content="oom test")
        strategy = ContextInjectionStrategy(
            backend=_make_backend((entry,)),
            config=MemoryRetrievalConfig(min_relevance=0.0),
            memory_filter=_MemoryErrorFilter(),
        )
        with pytest.raises(MemoryError):
            await strategy.prepare_messages(
                agent_id="agent-1",
                query_text="query",
                token_budget=5000,
            )


# -- Candidate pool multiplier ----------------------------------------


@pytest.mark.unit
class TestCandidatePoolMultiplier:
    def test_pool_limit_with_diversity_enabled(self) -> None:
        strategy = ContextInjectionStrategy(
            backend=_make_backend(),
            config=MemoryRetrievalConfig(
                diversity_penalty_enabled=True,
                candidate_pool_multiplier=3,
                max_memories=10,
            ),
        )
        assert strategy._compute_pool_limit() == 30

    def test_pool_limit_without_diversity(self) -> None:
        strategy = ContextInjectionStrategy(
            backend=_make_backend(),
            config=MemoryRetrievalConfig(
                diversity_penalty_enabled=False,
                max_memories=10,
            ),
        )
        assert strategy._compute_pool_limit() == 10

    def test_default_multiplier_is_three(self) -> None:
        config = MemoryRetrievalConfig()
        assert config.candidate_pool_multiplier == 3

    def test_multiplier_above_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(candidate_pool_multiplier=11)

    def test_multiplier_below_min_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(candidate_pool_multiplier=0)
