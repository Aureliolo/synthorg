# mypy: disable-error-code="explicit-any"
"""Tests for LLM consolidation strategy."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.composite import (
    CompositeConsolidationStrategy,
)
from synthorg.memory.consolidation.config import LLMConsolidationConfig
from synthorg.memory.consolidation.llm_op import LLMSynthesisOp
from synthorg.memory.consolidation.selectors import HighestRelevanceSelector
from synthorg.memory.consolidation.strategy import ConsolidationStrategy
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.providers.enums import FinishReason
from synthorg.providers.errors import (
    AuthenticationError,
    RateLimitError,
)
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.protocol import CompletionProvider


def _make_entry(  # noqa: PLR0913
    *,
    entry_id: str = "mem-1",
    agent_id: str = "agent-1",
    content: str = "test memory",
    category: MemoryCategory = MemoryCategory.EPISODIC,
    created_at: datetime | None = None,
    relevance_score: float | None = None,
) -> MemoryEntry:
    """Helper to build a MemoryEntry with sensible defaults."""
    return MemoryEntry(
        id=entry_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=MemoryMetadata(),
        created_at=created_at or datetime.now(UTC),
        relevance_score=relevance_score,
    )


def _make_response(content: str = "synthesized summary") -> CompletionResponse:
    """Build a mock CompletionResponse."""
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
        model="test-model",
    )


def _make_strategy(
    *,
    provider: AsyncMock | None = None,
    backend: AsyncMock | None = None,
    config: LLMConsolidationConfig | None = None,
    **config_overrides: Any,
) -> CompositeConsolidationStrategy:
    """Build the LLM composite (selector + LLMSynthesisOp) with mocks.

    Post ADR-0005: "llm" is
    ``Composite(HighestRelevanceSelector, LLMSynthesisOp, parallel=True)``.
    The selector's ``group_threshold`` is sourced from the
    ``LLMConsolidationConfig`` (``ge=3``), so an out-of-range override
    still raises ``ValidationError`` at config construction -- before
    the composite is built -- exactly as the pre-split monolith did.

    Pass ``config`` for a full override, or individual
    ``config_overrides`` kwargs (e.g. ``group_threshold=5``) forwarded
    to ``LLMConsolidationConfig``.
    """
    if backend is None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="summary-id-1")
        backend.delete = AsyncMock(return_value=True)
    if provider is None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())
    if config is None:
        config = LLMConsolidationConfig(**config_overrides)
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=config.group_threshold),
        op=LLMSynthesisOp(
            backend=backend,
            provider=provider,
            model="test-model",
            config=config,
        ),
        parallel=True,
    )


@pytest.mark.unit
class TestLLMConsolidationStrategyProtocol:
    def test_is_consolidation_strategy(self) -> None:
        strategy = _make_strategy()
        assert isinstance(strategy, ConsolidationStrategy)


@pytest.mark.unit
class TestLLMConsolidationStrategyInit:
    def test_group_threshold_below_min_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_strategy(group_threshold=2)

    def test_group_threshold_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_strategy(group_threshold=1)

    def test_group_threshold_three_accepted(self) -> None:
        # ge=3 is the minimum; constructing the composite must not
        # raise, and the config carries the value the selector uses.
        _make_strategy(group_threshold=3)
        assert LLMConsolidationConfig(group_threshold=3).group_threshold == 3


@pytest.mark.unit
class TestLLMConsolidationStrategyConsolidate:
    async def test_empty_input_returns_empty_result(self) -> None:
        strategy = _make_strategy()
        result = await strategy.consolidate((), agent_id="agent-1")
        assert result.consolidated_count == 0
        assert result.summary_id is None

    async def test_below_threshold_skipped(self) -> None:
        strategy = _make_strategy(group_threshold=3)
        entries = tuple(_make_entry(entry_id=f"e{i}") for i in range(2))
        result = await strategy.consolidate(entries, agent_id="agent-1")
        assert result.consolidated_count == 0

    async def test_above_threshold_consolidates(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="summary-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
        )

        now = datetime.now(UTC)
        entries = tuple(
            _make_entry(
                entry_id=f"e{i}",
                relevance_score=0.5 + i * 0.1,
                created_at=now,
            )
            for i in range(4)
        )

        result = await strategy.consolidate(entries, agent_id="agent-1")

        # Keeps best (e3 with 0.8 relevance), removes e0, e1, e2
        assert result.consolidated_count == 3
        assert result.summary_id == "summary-1"
        assert "e3" not in result.removed_ids

        # LLM was called
        provider.complete.assert_called_once()
        # Summary was stored
        backend.store.assert_called_once()
        # Removed entries were deleted
        assert backend.delete.call_count == 3

    async def test_multi_category_groups_independent(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        store_ids = iter(["sum-ep", "sum-sem"])
        backend.store = AsyncMock(side_effect=lambda *_a, **_kw: next(store_ids))
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
        )

        now = datetime.now(UTC)
        episodic = tuple(
            _make_entry(
                entry_id=f"ep{i}",
                category=MemoryCategory.EPISODIC,
                relevance_score=0.5,
                created_at=now,
            )
            for i in range(3)
        )
        semantic = tuple(
            _make_entry(
                entry_id=f"sem{i}",
                category=MemoryCategory.SEMANTIC,
                relevance_score=0.5,
                created_at=now,
            )
            for i in range(3)
        )

        result = await strategy.consolidate(
            (*episodic, *semantic),
            agent_id="agent-1",
        )

        # 2 entries removed per group (keep 1), 2 groups
        assert result.consolidated_count == 4
        assert provider.complete.call_count == 2
        assert backend.store.call_count == 2

    async def test_keeps_highest_relevance_entry(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
        )

        now = datetime.now(UTC)
        entries = (
            _make_entry(entry_id="low", relevance_score=0.3, created_at=now),
            _make_entry(entry_id="high", relevance_score=0.9, created_at=now),
            _make_entry(entry_id="mid", relevance_score=0.6, created_at=now),
        )

        result = await strategy.consolidate(entries, agent_id="agent-1")

        # "high" is kept, "low" and "mid" are removed
        assert "high" not in result.removed_ids
        assert "low" in result.removed_ids
        assert "mid" in result.removed_ids


@pytest.mark.unit
class TestLLMConsolidationStrategyErrorHandling:
    async def test_retryable_error_falls_back(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(
            side_effect=RateLimitError("rate limited"),
        )

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
        )

        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )

        # Should not raise -- falls back to concatenation
        result = await strategy.consolidate(entries, agent_id="agent-1")
        assert result.consolidated_count == 2

        # Verify fallback content was stored (starts with "Consolidated")
        stored_request = backend.store.call_args.args[1]
        assert stored_request.content.startswith("Consolidated")

    async def test_non_retryable_error_propagates(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(
            side_effect=AuthenticationError("bad key"),
        )

        strategy = _make_strategy(
            backend=AsyncMock(spec=MemoryBackend),
            provider=provider,
            group_threshold=3,
        )

        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )

        with pytest.raises(AuthenticationError):
            await strategy.consolidate(entries, agent_id="agent-1")

    async def test_no_deletes_on_synthesis_failure(self) -> None:
        """Non-retryable synthesis error must not delete any originals.

        Verifies the synthesize-before-delete ordering: if ``_synthesize``
        raises a non-retryable ``ProviderError``, no entries are deleted
        and data is preserved for the next consolidation pass.
        """
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=AuthenticationError("bad key"))

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )

        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )

        with pytest.raises(AuthenticationError):
            await strategy.consolidate(entries, agent_id="agent-1")

        # Synthesis raised before any deletes -- originals must be intact.
        backend.delete.assert_not_called()
        backend.store.assert_not_called()

    async def test_store_failure_after_synthesis_propagates_no_deletes(self) -> None:
        """If ``backend.store`` fails after successful synthesis, no deletes run.

        The synthesize-before-delete ordering is specifically designed
        so a ``store`` failure leaves originals intact.  Regression guard
        against future refactors that would reorder delete/store.
        """
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(side_effect=RuntimeError("store crashed"))
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())
        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )
        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )
        with pytest.raises(RuntimeError, match="store crashed"):
            await strategy.consolidate(entries, agent_id="agent-1")
        backend.delete.assert_not_called()

    async def test_memory_error_propagates(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=MemoryError("oom"))

        strategy = _make_strategy(
            backend=AsyncMock(spec=MemoryBackend),
            provider=provider,
            group_threshold=3,
        )

        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )

        with pytest.raises(MemoryError):
            await strategy.consolidate(entries, agent_id="agent-1")

    @pytest.mark.parametrize("empty_content", ["", "   \n  "])
    async def test_empty_or_whitespace_llm_response_falls_back(
        self,
        empty_content: str,
    ) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(
            return_value=_make_response(content=empty_content),
        )

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
        )

        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )

        result = await strategy.consolidate(entries, agent_id="agent-1")
        assert result.consolidated_count == 2

        stored_request = backend.store.call_args.args[1]
        assert stored_request.content.startswith("Consolidated")

    async def test_unexpected_error_falls_back(self) -> None:
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=RuntimeError("oops"))

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
        )

        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )

        result = await strategy.consolidate(entries, agent_id="agent-1")
        assert result.consolidated_count == 2

    async def test_recursion_error_propagates(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(side_effect=RecursionError("deep"))

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )
        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )

        with pytest.raises(RecursionError):
            await strategy.consolidate(entries, agent_id="agent-1")


@pytest.mark.unit
class TestLLMConsolidationStrategyDetails:
    async def test_select_entries_tiebreaker_prefers_most_recent(self) -> None:
        """Equal relevance scores: most-recent created_at wins."""
        from datetime import timedelta

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )

        base = datetime.now(UTC) - timedelta(hours=5)
        entries = (
            _make_entry(entry_id="old", relevance_score=0.5, created_at=base),
            _make_entry(
                entry_id="mid",
                relevance_score=0.5,
                created_at=base + timedelta(hours=1),
            ),
            _make_entry(
                entry_id="new",
                relevance_score=0.5,
                created_at=base + timedelta(hours=2),
            ),
        )
        result = await strategy.consolidate(entries, agent_id="agent-1")
        # "new" is the most recent among equal relevance: it stays.
        assert "new" not in result.removed_ids
        assert "old" in result.removed_ids
        assert "mid" in result.removed_ids

    async def test_select_entries_none_relevance_treated_as_zero(self) -> None:
        """Entry with explicit score beats entries with ``None`` relevance."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )
        entries = (
            _make_entry(entry_id="n1", relevance_score=None),
            _make_entry(entry_id="n2", relevance_score=None),
            _make_entry(entry_id="scored", relevance_score=0.1),
        )
        result = await strategy.consolidate(entries, agent_id="agent-1")
        assert "scored" not in result.removed_ids

    async def test_fallback_summary_truncates_long_content(self) -> None:
        """Fallback concat truncates entries longer than the cap."""
        long_content = "x" * 500
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(
            side_effect=RateLimitError("rate limited"),
        )
        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )
        entries = tuple(
            _make_entry(
                entry_id=f"e{i}",
                content=long_content,
                relevance_score=0.5,
            )
            for i in range(3)
        )
        await strategy.consolidate(entries, agent_id="agent-1")

        stored_request = backend.store.call_args.args[1]
        assert "..." in stored_request.content
        # 500-char raw content must NOT appear whole in the output.
        assert long_content not in stored_request.content

    async def test_successful_synthesis_stores_llm_response_verbatim(
        self,
    ) -> None:
        """Stored summary content equals (stripped) LLM response."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(
            return_value=_make_response(content="  synthesized answer  "),
        )
        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )
        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )
        await strategy.consolidate(entries, agent_id="agent-1")

        stored_request = backend.store.call_args.args[1]
        assert stored_request.content == "synthesized answer"
        assert stored_request.metadata.source == "consolidation"
        assert "llm-synthesized" in stored_request.metadata.tags

    async def test_fallback_tagged_as_concat_fallback(self) -> None:
        """Fallback summaries use the ``concat-fallback`` tag."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response(content=""))
        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )
        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )
        await strategy.consolidate(entries, agent_id="agent-1")

        stored_request = backend.store.call_args.args[1]
        assert "concat-fallback" in stored_request.metadata.tags
        assert "llm-synthesized" not in stored_request.metadata.tags

    async def test_partial_delete_failure_records_only_deleted_ids(self) -> None:
        """When a delete fails mid-loop, only successful IDs are reported."""
        from synthorg.memory.errors import MemoryStoreError

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        # 3 entries: first succeeds, second fails, third succeeds.
        backend.delete = AsyncMock(
            side_effect=[True, MemoryStoreError("disk full"), True],
        )
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=4,
            include_distillation_context=False,
        )
        entries = tuple(
            _make_entry(
                entry_id=f"e{i}",
                relevance_score=0.5 + i * 0.01,
            )
            for i in range(4)
        )
        result = await strategy.consolidate(entries, agent_id="agent-1")

        # 4 entries, best kept, 3 attempted deletes: 2 succeeded.
        assert result.consolidated_count == 2
        # Summary was still stored -- consolidation degrades gracefully.
        backend.store.assert_called_once()

    async def test_trajectory_context_fetched_from_backend(self) -> None:
        """Distillation entries are fetched and included in the prompt."""
        from synthorg.memory.models import MemoryEntry

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        distillation_entry = MemoryEntry(
            id="dist-1",
            agent_id="agent-1",
            category=MemoryCategory.EPISODIC,
            content="Distillation: Task completed. Trajectory: 3 turns.",
            metadata=MemoryMetadata(source="distillation", tags=("distillation",)),
            created_at=datetime.now(UTC),
        )
        backend.retrieve = AsyncMock(return_value=(distillation_entry,))

        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=True,
        )
        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )
        await strategy.consolidate(entries, agent_id="agent-1")

        # Verify distillation lookup happened.
        backend.retrieve.assert_called_once()
        # Verify the LLM call's system prompt includes trajectory context.
        messages = provider.complete.call_args.args[0]
        system_prompt = messages[0].content
        assert "trajectory context" in system_prompt.lower()
        assert "Task completed" in system_prompt

    async def test_trajectory_context_disabled_skips_lookup(self) -> None:
        """``include_distillation_context=False`` skips the backend query."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        backend.retrieve = AsyncMock(return_value=())
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=False,
        )
        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )
        await strategy.consolidate(entries, agent_id="agent-1")

        backend.retrieve.assert_not_called()

    async def test_trajectory_context_lookup_failure_degrades(self) -> None:
        """A failed distillation lookup degrades to plain synthesis."""
        from synthorg.memory.errors import MemoryRetrievalError

        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        backend.retrieve = AsyncMock(side_effect=MemoryRetrievalError("db down"))
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            group_threshold=3,
            include_distillation_context=True,
        )
        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )
        result = await strategy.consolidate(entries, agent_id="agent-1")

        # Consolidation still succeeds -- lookup failure degraded quietly.
        assert result.consolidated_count == 2

    async def test_trajectory_context_entry_limit_and_truncation(self) -> None:
        """Config entry limits and per-entry char cap are enforced.

        The backend query asks for at most 5 entries, and each snippet
        is truncated to ~500 chars before being embedded in the system
        prompt.  Regression guard against silent prompt-size blowups.
        """
        cfg = LLMConsolidationConfig()

        # Build an oversized trajectory entry (1000 chars) to verify truncation.
        long_content = "x" * 1000
        oversized = _make_entry(
            entry_id="dist-big",
            content=long_content,
        )
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="sum-1")
        backend.delete = AsyncMock(return_value=True)
        backend.retrieve = AsyncMock(return_value=(oversized,))
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = AsyncMock(return_value=_make_response())

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            include_distillation_context=True,
        )
        entries = tuple(
            _make_entry(entry_id=f"e{i}", relevance_score=0.5) for i in range(3)
        )
        await strategy.consolidate(entries, agent_id="agent-1")

        # Backend query uses an over-fetch factor so the local sort by
        # created_at has enough candidates to pick the N most recent;
        # the final slice still caps at config.max_trajectory_context_entries.
        retrieve_args = backend.retrieve.call_args
        query = retrieve_args.args[1]
        assert query.limit >= cfg.max_trajectory_context_entries

        # The embedded snippet was truncated to the per-entry char cap.
        messages = provider.complete.call_args.args[0]
        system_prompt = messages[0].content
        assert system_prompt is not None
        # The full 1000-char run of 'x' should NOT appear in the prompt;
        # only the truncated 500-char prefix should.
        assert "x" * 1000 not in system_prompt
        assert "x" * cfg.max_trajectory_chars_per_entry in system_prompt

    async def test_multi_category_groups_processed_in_parallel(self) -> None:
        """Multiple category groups are actually processed concurrently.

        Uses an ``asyncio.Event`` barrier: each provider call increments
        a counter and then waits for the counter to reach the expected
        concurrency level before returning.  A sequential
        implementation would deadlock here (the second call never
        starts until the first returns, and the first waits forever for
        the second), so the test will time out; a parallel
        ``TaskGroup`` implementation satisfies the barrier and the test
        passes.
        """
        expected_concurrent = 2
        barrier = asyncio.Event()
        in_flight = 0
        max_in_flight = 0

        async def _barrier_complete(*args: object, **kwargs: object) -> object:
            del args, kwargs
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            if in_flight >= expected_concurrent:
                barrier.set()
            # Wait until every expected call has entered before any
            # returns.  Sequential execution fails this because
            # ``barrier`` never gets set by a second caller.
            await barrier.wait()
            in_flight -= 1
            return _make_response()

        backend = AsyncMock(spec=MemoryBackend)
        store_ids = iter(["sum-a", "sum-b"])
        backend.store = AsyncMock(side_effect=lambda *_a, **_kw: next(store_ids))
        backend.delete = AsyncMock(return_value=True)
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete = _barrier_complete

        strategy = _make_strategy(
            backend=backend,
            provider=provider,
            include_distillation_context=False,
        )

        now = datetime.now(UTC)
        episodic = tuple(
            _make_entry(
                entry_id=f"ep{i}",
                category=MemoryCategory.EPISODIC,
                relevance_score=0.5,
                created_at=now,
            )
            for i in range(3)
        )
        semantic = tuple(
            _make_entry(
                entry_id=f"sem{i}",
                category=MemoryCategory.SEMANTIC,
                relevance_score=0.5,
                created_at=now,
            )
            for i in range(3)
        )
        result = await strategy.consolidate(
            (*episodic, *semantic),
            agent_id="agent-1",
        )

        assert max_in_flight == expected_concurrent, (
            f"expected {expected_concurrent} provider calls in-flight "
            f"concurrently, saw peak={max_in_flight} -- groups were "
            "processed sequentially, not via TaskGroup"
        )
        assert backend.store.call_count == expected_concurrent
        assert result.consolidated_count == 4
