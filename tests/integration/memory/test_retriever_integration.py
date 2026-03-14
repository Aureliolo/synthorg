"""Integration tests for ContextInjectionStrategy end-to-end pipeline.

Uses in-memory mock backends that simulate realistic memory entries
to test the full retrieve → rank → format pipeline.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.formatter import MEMORY_BLOCK_END, MEMORY_BLOCK_START
from synthorg.memory.injection import DefaultTokenEstimator
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.providers.enums import MessageRole

pytestmark = pytest.mark.timeout(30)


class InMemoryBackend:
    """Minimal in-memory MemoryBackend for integration tests."""

    def __init__(self, entries: tuple[MemoryEntry, ...] = ()) -> None:
        self._entries = entries
        self._connected = True

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def health_check(self) -> bool:
        return self._connected

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def backend_name(self) -> str:
        return "in-memory-test"

    async def store(
        self,
        agent_id: str,
        request: MemoryStoreRequest,
    ) -> str:
        return "stored-id"

    async def retrieve(
        self,
        agent_id: str,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        results = [e for e in self._entries if e.agent_id == agent_id]
        if query.categories:
            results = [e for e in results if e.category in query.categories]
        return tuple(results[: query.limit])

    async def get(
        self,
        agent_id: str,
        memory_id: str,
    ) -> MemoryEntry | None:
        for e in self._entries:
            if e.id == memory_id and e.agent_id == agent_id:
                return e
        return None

    async def delete(self, agent_id: str, memory_id: str) -> bool:
        return False

    async def count(
        self,
        agent_id: str,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        return len(
            [
                e
                for e in self._entries
                if e.agent_id == agent_id
                and (category is None or e.category == category)
            ]
        )


class InMemorySharedStore:
    """Minimal in-memory SharedKnowledgeStore for integration tests."""

    def __init__(self, entries: tuple[MemoryEntry, ...] = ()) -> None:
        self._entries = entries

    async def publish(
        self,
        agent_id: str,
        request: MemoryStoreRequest,
    ) -> str:
        return "shared-id"

    async def search_shared(
        self,
        query: MemoryQuery,
        *,
        exclude_agent: str | None = None,
    ) -> tuple[MemoryEntry, ...]:
        results = [
            e
            for e in self._entries
            if exclude_agent is None or e.agent_id != exclude_agent
        ]
        return tuple(results[: query.limit])

    async def retract(self, agent_id: str, memory_id: str) -> bool:
        return False


def _make_entry(  # noqa: PLR0913
    *,
    entry_id: str,
    agent_id: str = "agent-1",
    content: str,
    category: MemoryCategory = MemoryCategory.EPISODIC,
    created_at: datetime | None = None,
    relevance_score: float | None = 0.7,
) -> MemoryEntry:
    """Build a MemoryEntry with defaults."""
    return MemoryEntry(
        id=entry_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=MemoryMetadata(),
        created_at=created_at or datetime.now(UTC),
        relevance_score=relevance_score,
    )


@pytest.mark.integration
class TestRetrieverIntegrationEndToEnd:
    """End-to-end pipeline tests with realistic mock backends."""

    async def test_full_pipeline_produces_formatted_output(self) -> None:
        now = datetime.now(UTC)
        entries = (
            _make_entry(
                entry_id="e1",
                content="Python best practices",
                category=MemoryCategory.SEMANTIC,
                created_at=now - timedelta(hours=1),
                relevance_score=0.9,
            ),
            _make_entry(
                entry_id="e2",
                content="Last meeting notes",
                category=MemoryCategory.EPISODIC,
                created_at=now - timedelta(hours=24),
                relevance_score=0.6,
            ),
            _make_entry(
                entry_id="e3",
                content="Git workflow reminder",
                category=MemoryCategory.PROCEDURAL,
                created_at=now - timedelta(hours=72),
                relevance_score=0.5,
            ),
        )
        backend = InMemoryBackend(entries)
        config = MemoryRetrievalConfig(min_relevance=0.0)
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )

        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="what should I know",
            token_budget=2000,
        )

        assert len(result) == 1
        msg = result[0]
        assert msg.role is MessageRole.SYSTEM
        content = msg.content
        assert content is not None
        assert MEMORY_BLOCK_START in content
        assert MEMORY_BLOCK_END in content
        # Most relevant should appear first
        assert "Python best practices" in content
        assert "Last meeting notes" in content
        assert "Git workflow reminder" in content

    async def test_pipeline_with_shared_memories(self) -> None:
        now = datetime.now(UTC)
        personal = (
            _make_entry(
                entry_id="p1",
                content="My personal knowledge",
                created_at=now,
                relevance_score=0.8,
            ),
        )
        shared = (
            _make_entry(
                entry_id="s1",
                agent_id="agent-other",
                content="Team shared fact",
                created_at=now,
                relevance_score=0.7,
            ),
        )

        backend = InMemoryBackend(personal)
        shared_store = InMemorySharedStore(shared)
        config = MemoryRetrievalConfig(
            include_shared=True,
            min_relevance=0.0,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
            shared_store=shared_store,
        )

        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="team knowledge",
            token_budget=2000,
        )

        assert len(result) == 1
        content = result[0].content
        assert content is not None
        assert "My personal knowledge" in content
        assert "Team shared fact" in content
        assert "[shared]" in content

    async def test_no_memories_returns_empty(self) -> None:
        backend = InMemoryBackend(())
        config = MemoryRetrievalConfig()
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )

        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="anything",
            token_budget=1000,
        )

        assert result == ()

    async def test_recency_ranking_order(self) -> None:
        """Recent memories rank higher when recency weight is high."""
        now = datetime.now(UTC)
        old_but_relevant = _make_entry(
            entry_id="old",
            content="old knowledge",
            created_at=now - timedelta(hours=200),
            relevance_score=0.6,
        )
        recent_less_relevant = _make_entry(
            entry_id="recent",
            content="fresh insight",
            created_at=now,
            relevance_score=0.4,
        )

        backend = InMemoryBackend((old_but_relevant, recent_less_relevant))
        config = MemoryRetrievalConfig(
            relevance_weight=0.3,
            recency_weight=0.7,
            min_relevance=0.0,
            personal_boost=0.0,
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )

        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="knowledge",
            token_budget=2000,
        )

        assert len(result) == 1
        text = result[0].content
        assert text is not None
        # Fresh insight should appear before old knowledge
        assert text.index("fresh insight") < text.index("old knowledge")

    async def test_token_budget_limits_output(self) -> None:
        """Pipeline respects token budget, including fewer memories."""
        now = datetime.now(UTC)
        entries = tuple(
            _make_entry(
                entry_id=f"e{i}",
                content=f"Memory number {i} with enough content to take space " * 3,
                created_at=now - timedelta(hours=i),
                relevance_score=0.9 - i * 0.01,
            )
            for i in range(20)
        )
        backend = InMemoryBackend(entries)
        config = MemoryRetrievalConfig(min_relevance=0.0)

        # Large budget
        strategy_large = ContextInjectionStrategy(
            backend=backend,
            config=config,
            token_estimator=DefaultTokenEstimator(),
        )
        large_result = await strategy_large.prepare_messages(
            agent_id="agent-1",
            query_text="all",
            token_budget=10000,
        )

        # Small budget
        strategy_small = ContextInjectionStrategy(
            backend=backend,
            config=config,
            token_estimator=DefaultTokenEstimator(),
        )
        small_result = await strategy_small.prepare_messages(
            agent_id="agent-1",
            query_text="all",
            token_budget=200,
        )

        assert len(large_result) == 1
        assert len(small_result) == 1
        small_content = small_result[0].content
        large_content = large_result[0].content
        assert small_content is not None
        assert large_content is not None
        # Small budget should have fewer content lines
        assert len(small_content) < len(large_content)

    async def test_category_filter_works(self) -> None:
        """Category filter limits which memories are retrieved."""
        now = datetime.now(UTC)
        entries = (
            _make_entry(
                entry_id="sem1",
                content="semantic fact",
                category=MemoryCategory.SEMANTIC,
                created_at=now,
            ),
            _make_entry(
                entry_id="epi1",
                content="episodic event",
                category=MemoryCategory.EPISODIC,
                created_at=now,
            ),
        )
        backend = InMemoryBackend(entries)
        config = MemoryRetrievalConfig(min_relevance=0.0)
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=config,
        )

        result = await strategy.prepare_messages(
            agent_id="agent-1",
            query_text="facts",
            token_budget=2000,
            categories=frozenset({MemoryCategory.SEMANTIC}),
        )

        assert len(result) == 1
        content = result[0].content
        assert content is not None
        assert "semantic fact" in content
        assert "episodic event" not in content
