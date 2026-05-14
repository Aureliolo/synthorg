"""Tests for memory formatter."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.engine.prompt_safety import (
    TAG_MEMORY_ENTRY,
    untrusted_content_directive,
)
from synthorg.memory.formatter import (
    format_memory_context,
    format_memory_context_with_directive,
)
from synthorg.memory.injection import (
    DefaultTokenEstimator,
    InjectionPoint,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.ranking import ScoredMemory
from synthorg.providers.enums import MessageRole


def _make_scored(
    *,
    content: str = "test memory content",
    category: MemoryCategory = MemoryCategory.EPISODIC,
    combined_score: float = 0.8,
    is_shared: bool = False,
) -> ScoredMemory:
    """Helper to build a ScoredMemory."""
    now = datetime.now(UTC)
    entry = MemoryEntry(
        id="mem-1",
        agent_id="agent-1",
        category=category,
        content=content,
        metadata=MemoryMetadata(),
        created_at=now,
        relevance_score=combined_score,
    )
    return ScoredMemory(
        entry=entry,
        relevance_score=combined_score,
        recency_score=1.0,
        combined_score=combined_score,
        is_shared=is_shared,
    )


@pytest.mark.unit
class TestFormatMemoryContext:
    def test_empty_memories_returns_empty(self) -> None:
        result = format_memory_context(
            (),
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        assert result == ()

    def test_single_memory_produces_one_message(self) -> None:
        memories = (_make_scored(content="Remember this"),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        assert len(result) == 1
        assert result[0].role is MessageRole.SYSTEM
        content = result[0].content
        assert content is not None
        assert f"<{TAG_MEMORY_ENTRY}>" in content
        assert f"</{TAG_MEMORY_ENTRY}>" in content
        assert "Remember this" in content

    def test_category_label_present(self) -> None:
        memories = (
            _make_scored(
                content="test",
                category=MemoryCategory.SEMANTIC,
            ),
        )
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        content = result[0].content
        assert content is not None
        assert "semantic" in content

    def test_score_label_present(self) -> None:
        memories = (_make_scored(combined_score=0.85),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        content = result[0].content
        assert content is not None
        assert "0.85" in content

    def test_shared_prefix_present(self) -> None:
        memories = (_make_scored(is_shared=True),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        content = result[0].content
        assert content is not None
        assert "[shared]" in content

    def test_non_shared_no_prefix(self) -> None:
        memories = (_make_scored(is_shared=False),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        content = result[0].content
        assert content is not None
        assert "[shared]" not in content

    def test_multiple_memories_in_order(self) -> None:
        m1 = _make_scored(content="first memory", combined_score=0.9)
        m2 = _make_scored(content="second memory", combined_score=0.7)
        result = format_memory_context(
            (m1, m2),
            estimator=DefaultTokenEstimator(),
            token_budget=5000,
        )
        assert len(result) == 1
        content = result[0].content
        assert content is not None
        idx1 = content.index("first memory")
        idx2 = content.index("second memory")
        assert idx1 < idx2

    def test_each_memory_individually_fenced(self) -> None:
        """Each included memory gets its own ``<memory-entry>`` fence."""
        memories = (
            _make_scored(content="alpha", combined_score=0.9),
            _make_scored(content="beta", combined_score=0.7),
        )
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=5000,
        )
        content = result[0].content
        assert content is not None
        assert content.count(f"<{TAG_MEMORY_ENTRY}>") == 2
        assert content.count(f"</{TAG_MEMORY_ENTRY}>") == 2

    def test_token_budget_limits_memories(self) -> None:
        """Only memories that fit within the budget are included."""
        memories = tuple(
            _make_scored(content="x" * 100, combined_score=0.9 - i * 0.01)
            for i in range(10)
        )
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=80,
        )
        assert len(result) == 1, "Expected at least some memories to fit"
        content = result[0].content
        assert content is not None
        # Each surviving entry contributes exactly one open + one close
        # fence; counting the close tag gives us the included count.
        included = content.count(f"</{TAG_MEMORY_ENTRY}>")
        assert 0 < included < 10

    def test_zero_budget_returns_empty(self) -> None:
        memories = (_make_scored(content="some content"),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=0,
        )
        assert result == ()

    def test_budget_too_small_for_any_returns_empty(self) -> None:
        """When budget cannot fit even one wrapped memory entry."""
        memories = (_make_scored(content="x" * 200),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=1,
        )
        assert result == ()

    def test_injection_point_user(self) -> None:
        memories = (_make_scored(content="user memory"),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
            injection_point=InjectionPoint.USER,
        )
        assert len(result) == 1
        assert result[0].role is MessageRole.USER

    def test_injection_point_default_system(self) -> None:
        memories = (_make_scored(content="sys memory"),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        assert result[0].role is MessageRole.SYSTEM

    def test_negative_budget_returns_empty(self) -> None:
        memories = (_make_scored(content="some content"),)
        result = format_memory_context(
            memories,
            estimator=DefaultTokenEstimator(),
            token_budget=-1,
        )
        assert result == ()

    def test_greedy_packing_skips_large_includes_small(self) -> None:
        """Greedy packing skips entries too large but includes smaller ones."""
        large = _make_scored(content="x" * 400, combined_score=0.95)
        small = _make_scored(content="short", combined_score=0.50)
        # Budget enough for one wrapped small entry but not the large one.
        result = format_memory_context(
            (large, small),
            estimator=DefaultTokenEstimator(),
            token_budget=30,
        )
        assert len(result) == 1
        content = result[0].content
        assert content is not None
        assert "short" in content
        assert "x" * 400 not in content

    def test_unsupported_injection_point_raises_value_error(self) -> None:
        """Unsupported InjectionPoint raises ValueError."""
        memories = (_make_scored(content="test"),)
        with pytest.raises(ValueError, match="Unsupported injection point"):
            format_memory_context(
                memories,
                estimator=DefaultTokenEstimator(),
                token_budget=1000,
                injection_point="bogus",  # type: ignore[arg-type]
            )


@pytest.mark.unit
class TestFormatMemoryContextWithDirective:
    """The directive-bundling helper that consumers should use."""

    def test_empty_memories_returns_empty(self) -> None:
        """No memories means no directive either; directive alone wastes tokens."""
        result = format_memory_context_with_directive(
            (),
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        assert result == ()

    def test_budget_too_small_returns_empty(self) -> None:
        """Same fail-closed behaviour as the underlying helper."""
        result = format_memory_context_with_directive(
            (_make_scored(content="x" * 200),),
            estimator=DefaultTokenEstimator(),
            token_budget=1,
        )
        assert result == ()

    def test_prepends_directive_system_message(self) -> None:
        """The first message is a SYSTEM directive for ``TAG_MEMORY_ENTRY``."""
        result = format_memory_context_with_directive(
            (_make_scored(content="hello"),),
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
        )
        assert len(result) == 2
        directive_message, memory_message = result
        assert directive_message.role is MessageRole.SYSTEM
        directive_text = directive_message.content
        assert directive_text is not None
        assert directive_text == untrusted_content_directive((TAG_MEMORY_ENTRY,))
        memory_content = memory_message.content
        assert memory_content is not None
        assert "hello" in memory_content

    def test_preserves_memory_role_under_user_injection(self) -> None:
        """``injection_point=USER`` keeps the memory message at USER role."""
        result = format_memory_context_with_directive(
            (_make_scored(content="user-side"),),
            estimator=DefaultTokenEstimator(),
            token_budget=1000,
            injection_point=InjectionPoint.USER,
        )
        assert len(result) == 2
        directive_message, memory_message = result
        # Directive always SYSTEM; memory message follows the requested role.
        assert directive_message.role is MessageRole.SYSTEM
        assert memory_message.role is MessageRole.USER
