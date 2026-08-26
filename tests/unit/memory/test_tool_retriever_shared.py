"""The ``search_memory`` tool must reach org knowledge, not personal only.

``build_memory_recall_tool`` builds an ``OrgSharedKnowledgeStore``, sets
``include_shared``, and hands both to ``ToolBasedInjectionStrategy``. The
strategy held the store in a slot and read it nowhere, so an agent was shown
org knowledge in its injected context and then given a tool that could not
reach the same store. A newly-hired agent owns no memories of its own, which
made the tool answer "No memories found." to every query it would ever be
given.

These assert the invariant rather than the run: the tool's result includes
what only the shared store holds, on every retrieval path it has.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.errors import MemoryRetrievalError
from synthorg.memory.injection import InjectionStrategy
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.reformulation import QueryReformulator, SufficiencyChecker
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.memory.tool_retriever import (
    SEARCH_MEMORY_TOOL_NAME,
    ToolBasedInjectionStrategy,
)

_AGENT = "agent-1"


def _entry(entry_id: str, content: str, relevance: float) -> MemoryEntry:
    """Build a memory entry.

    Returns:
        A minimal entry carrying *content* at *relevance*.
    """
    return MemoryEntry(
        id=entry_id,
        agent_id=_AGENT,
        category=MemoryCategory.SEMANTIC,
        content=content,
        metadata=MemoryMetadata(),
        created_at=datetime.now(UTC),
        relevance_score=relevance,
    )


def _backend(entries: tuple[MemoryEntry, ...] = ()) -> AsyncMock:
    """Build a memory backend returning *entries*.

    Returns:
        The stubbed backend.
    """
    backend = AsyncMock(spec=MemoryBackend)
    backend.retrieve = AsyncMock(return_value=entries)
    return backend


def _shared(entries: tuple[MemoryEntry, ...] = ()) -> AsyncMock:
    """Build a shared knowledge store returning *entries*.

    Returns:
        The stubbed store.
    """
    store = AsyncMock(spec=SharedKnowledgeStore)
    store.search_shared = AsyncMock(return_value=entries)
    return store


def _config(
    *,
    include_shared: bool = True,
    query_reformulation_enabled: bool = False,
    max_reformulation_rounds: int = 1,
) -> MemoryRetrievalConfig:
    """Build a tool-based retrieval config.

    Returns:
        The config for a ``search_memory`` strategy.
    """
    return MemoryRetrievalConfig(
        strategy=InjectionStrategy.TOOL_BASED,
        min_relevance=0.0,
        include_shared=include_shared,
        query_reformulation_enabled=query_reformulation_enabled,
        max_reformulation_rounds=max_reformulation_rounds,
    )


async def _search(
    strategy: ToolBasedInjectionStrategy,
    query: str = "how do we deploy",
) -> str:
    """Run one ``search_memory`` call.

    Returns:
        The formatted tool result.
    """
    return await strategy.handle_tool_call(
        SEARCH_MEMORY_TOOL_NAME,
        {"query": query},
        _AGENT,
    )


@pytest.mark.unit
class TestSharedStoreReachesTheTool:
    """The half the tool was built for, and had never read."""

    async def test_org_knowledge_reaches_an_agent_with_no_memories(self) -> None:
        # The shipped case: a newly-hired agent owns nothing, so personal-only
        # recall can never answer, however good the org's knowledge is.
        strategy = ToolBasedInjectionStrategy(
            backend=_backend(),
            config=_config(),
            shared_store=_shared((_entry("org-1", "deploy via the CLI", 0.9),)),
        )

        result = await _search(strategy)

        assert "deploy via the CLI" in result
        assert "No memories found" not in result

    async def test_personal_and_org_results_are_merged_best_first(self) -> None:
        strategy = ToolBasedInjectionStrategy(
            backend=_backend((_entry("own-1", "my own note", 0.4),)),
            config=_config(),
            shared_store=_shared((_entry("org-1", "the org playbook", 0.9),)),
        )

        result = await _search(strategy)

        assert result.index("the org playbook") < result.index("my own note")

    async def test_the_agent_is_excluded_from_its_own_shared_results(self) -> None:
        # Its own entries arrive through the personal read; counting them
        # twice would let one memory outrank the whole org.
        store = _shared()
        strategy = ToolBasedInjectionStrategy(
            backend=_backend(),
            config=_config(),
            shared_store=store,
        )

        await _search(strategy)

        assert store.search_shared.await_args is not None
        assert store.search_shared.await_args.kwargs["exclude_agent"] == _AGENT

    async def test_org_knowledge_is_not_read_when_the_config_excludes_it(
        self,
    ) -> None:
        store = _shared((_entry("org-1", "the org playbook", 0.9),))
        strategy = ToolBasedInjectionStrategy(
            backend=_backend(),
            config=_config(include_shared=False),
            shared_store=store,
        )

        result = await _search(strategy)

        store.search_shared.assert_not_awaited()
        assert "No memories found" in result

    async def test_a_failed_org_read_degrades_to_personal_rather_than_nothing(
        self,
    ) -> None:
        store = _shared()
        store.search_shared = AsyncMock(side_effect=MemoryRetrievalError("down"))
        strategy = ToolBasedInjectionStrategy(
            backend=_backend((_entry("own-1", "my own note", 0.4),)),
            config=_config(),
            shared_store=store,
        )

        result = await _search(strategy)

        assert "my own note" in result

    async def test_a_failed_personal_read_says_unavailable_not_empty(self) -> None:
        # An agent told the store is empty stops asking. One told the search
        # is unavailable does not, which is why these two answers differ.
        backend = _backend()
        backend.retrieve = AsyncMock(side_effect=MemoryRetrievalError("down"))
        strategy = ToolBasedInjectionStrategy(
            backend=backend,
            config=_config(),
            shared_store=_shared((_entry("org-1", "the org playbook", 0.9),)),
        )

        result = await _search(strategy)

        assert "No memories found" not in result
        assert "the org playbook" not in result


@pytest.mark.unit
class TestSharedStoreReachesEveryRetrievalPath:
    """Three retrieval sites, one owner: none may read personal alone."""

    @staticmethod
    def _reformulating(
        *,
        backend: AsyncMock,
        store: AsyncMock,
    ) -> ToolBasedInjectionStrategy:
        """Build a strategy whose Search-and-Ask loop runs one round.

        Returns:
            The strategy, wired to a reformulator that widens once then
            settles so both the round-0 and per-round reads happen.
        """
        reformulator = AsyncMock(spec=QueryReformulator)
        reformulator.reformulate = AsyncMock(
            side_effect=["how do we ship", "how do we ship"]
        )
        checker = AsyncMock(spec=SufficiencyChecker)
        checker.check_sufficiency = AsyncMock(return_value=False)
        return ToolBasedInjectionStrategy(
            backend=backend,
            config=_config(
                query_reformulation_enabled=True,
                max_reformulation_rounds=2,
            ),
            shared_store=store,
            reformulator=reformulator,
            sufficiency_checker=checker,
        )

    async def test_the_reformulation_loop_reads_org_knowledge_every_round(
        self,
    ) -> None:
        store = _shared((_entry("org-1", "the org playbook", 0.9),))
        strategy = self._reformulating(backend=_backend(), store=store)

        result = await _search(strategy)

        assert "the org playbook" in result
        # Round 0 plus the reformulated round: a fused round 0 that then
        # narrowed back to personal would still pass on the result alone.
        assert store.search_shared.await_count >= 2
