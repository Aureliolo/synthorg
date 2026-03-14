"""Unit tests for memory filter strategies."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.filter import (
    NON_INFERABLE_TAG,
    MemoryFilterStrategy,
    PassthroughMemoryFilter,
    TagBasedMemoryFilter,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.ranking import ScoredMemory

pytestmark = pytest.mark.timeout(30)


def _make_scored_memory(
    *,
    entry_id: str = "mem-1",
    tags: tuple[str, ...] = (),
    content: str = "test memory",
    combined_score: float = 0.8,
) -> ScoredMemory:
    """Build a ScoredMemory with specified tags."""
    entry = MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        category=MemoryCategory.EPISODIC,
        content=content,
        metadata=MemoryMetadata(tags=tags),
        created_at=datetime.now(UTC),
        relevance_score=0.8,
    )
    return ScoredMemory(
        entry=entry,
        relevance_score=0.8,
        recency_score=0.9,
        combined_score=combined_score,
    )


# ── Protocol compliance ──────────────────────────────────────────


@pytest.mark.unit
class TestProtocolCompliance:
    """Both filters satisfy the MemoryFilterStrategy protocol."""

    def test_tag_based_satisfies_protocol(self) -> None:
        assert isinstance(TagBasedMemoryFilter(), MemoryFilterStrategy)

    def test_passthrough_satisfies_protocol(self) -> None:
        assert isinstance(PassthroughMemoryFilter(), MemoryFilterStrategy)


# ── TagBasedMemoryFilter ──────────────────────────────────────────


@pytest.mark.unit
class TestTagBasedMemoryFilter:
    """Tests for the tag-based memory filter."""

    def test_retains_tagged_memories(self) -> None:
        tagged = _make_scored_memory(
            entry_id="m1",
            tags=(NON_INFERABLE_TAG,),
        )
        untagged = _make_scored_memory(entry_id="m2", tags=())
        filt = TagBasedMemoryFilter()

        result = filt.filter_for_injection((tagged, untagged))

        assert len(result) == 1
        assert result[0].entry.id == "m1"

    def test_excludes_all_untagged(self) -> None:
        untagged = _make_scored_memory(tags=("other-tag",))
        filt = TagBasedMemoryFilter()

        result = filt.filter_for_injection((untagged,))

        assert result == ()

    def test_retains_all_tagged(self) -> None:
        m1 = _make_scored_memory(
            entry_id="m1",
            tags=(NON_INFERABLE_TAG, "extra"),
        )
        m2 = _make_scored_memory(
            entry_id="m2",
            tags=(NON_INFERABLE_TAG,),
        )
        filt = TagBasedMemoryFilter()

        result = filt.filter_for_injection((m1, m2))

        assert len(result) == 2

    def test_custom_required_tag(self) -> None:
        memory = _make_scored_memory(tags=("custom-tag",))
        filt = TagBasedMemoryFilter(required_tag="custom-tag")

        result = filt.filter_for_injection((memory,))

        assert len(result) == 1

    def test_empty_input_returns_empty(self) -> None:
        filt = TagBasedMemoryFilter()
        result = filt.filter_for_injection(())
        assert result == ()

    def test_strategy_name(self) -> None:
        assert TagBasedMemoryFilter().strategy_name == "tag_based"


# ── PassthroughMemoryFilter ──────────────────────────────────────


@pytest.mark.unit
class TestPassthroughMemoryFilter:
    """Tests for the passthrough (no-op) memory filter."""

    def test_returns_all_unchanged(self) -> None:
        m1 = _make_scored_memory(entry_id="m1")
        m2 = _make_scored_memory(entry_id="m2")
        filt = PassthroughMemoryFilter()

        result = filt.filter_for_injection((m1, m2))

        assert len(result) == 2
        assert result[0].entry.id == "m1"
        assert result[1].entry.id == "m2"

    def test_empty_input_returns_empty(self) -> None:
        filt = PassthroughMemoryFilter()
        result = filt.filter_for_injection(())
        assert result == ()

    def test_strategy_name(self) -> None:
        assert PassthroughMemoryFilter().strategy_name == "passthrough"


# ── NON_INFERABLE_TAG constant ───────────────────────────────────


@pytest.mark.unit
class TestNonInferableTag:
    """Tests for the NON_INFERABLE_TAG constant."""

    def test_value(self) -> None:
        assert NON_INFERABLE_TAG == "non-inferable"
