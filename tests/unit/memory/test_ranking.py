"""Tests for memory ranking functions."""

import math
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.core.enums import MemoryCategory
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.ranking import (
    ScoredMemory,
    compute_combined_score,
    compute_recency_score,
    rank_memories,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig

pytestmark = pytest.mark.timeout(30)


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


# ── compute_recency_score ───────────────────────────────────────


@pytest.mark.unit
class TestComputeRecencyScore:
    def test_zero_age_returns_one(self) -> None:
        now = datetime.now(UTC)
        assert compute_recency_score(now, now, decay_rate=0.01) == 1.0

    def test_one_hour_decay(self) -> None:
        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)
        score = compute_recency_score(one_hour_ago, now, decay_rate=0.01)
        expected = math.exp(-0.01 * 1.0)
        assert abs(score - expected) < 1e-9

    def test_24_hour_decay(self) -> None:
        now = datetime.now(UTC)
        day_ago = now - timedelta(hours=24)
        score = compute_recency_score(day_ago, now, decay_rate=0.01)
        expected = math.exp(-0.01 * 24.0)
        assert abs(score - expected) < 1e-9

    def test_72_hour_decay(self) -> None:
        now = datetime.now(UTC)
        three_days_ago = now - timedelta(hours=72)
        score = compute_recency_score(three_days_ago, now, decay_rate=0.01)
        expected = math.exp(-0.01 * 72.0)
        assert abs(score - expected) < 1e-9
        assert score < 0.5  # ~0.49

    def test_zero_decay_rate_always_one(self) -> None:
        now = datetime.now(UTC)
        old = now - timedelta(hours=1000)
        assert compute_recency_score(old, now, decay_rate=0.0) == 1.0

    def test_high_decay_rate_approaches_zero(self) -> None:
        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)
        score = compute_recency_score(one_hour_ago, now, decay_rate=10.0)
        assert score < 0.001

    def test_future_created_at_clamped_to_one(self) -> None:
        """Entries with future timestamps get recency 1.0."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=1)
        score = compute_recency_score(future, now, decay_rate=0.01)
        assert score == 1.0


# ── compute_combined_score ──────────────────────────────────────


@pytest.mark.unit
class TestComputeCombinedScore:
    def test_default_weights(self) -> None:
        score = compute_combined_score(0.8, 0.6, 0.7, 0.3)
        expected = 0.7 * 0.8 + 0.3 * 0.6
        assert abs(score - expected) < 1e-9

    def test_all_relevance(self) -> None:
        score = compute_combined_score(0.9, 0.1, 1.0, 0.0)
        assert abs(score - 0.9) < 1e-9

    def test_all_recency(self) -> None:
        score = compute_combined_score(0.9, 0.1, 0.0, 1.0)
        assert abs(score - 0.1) < 1e-9

    def test_equal_weights(self) -> None:
        score = compute_combined_score(0.8, 0.4, 0.5, 0.5)
        assert abs(score - 0.6) < 1e-9


# ── ScoredMemory model ─────────────────────────────────────────


@pytest.mark.unit
class TestScoredMemory:
    def test_creation(self) -> None:
        entry = _make_entry()
        sm = ScoredMemory(
            entry=entry,
            relevance_score=0.8,
            recency_score=0.9,
            combined_score=0.85,
        )
        assert sm.relevance_score == 0.8
        assert sm.is_shared is False

    def test_frozen(self) -> None:
        entry = _make_entry()
        sm = ScoredMemory(
            entry=entry,
            relevance_score=0.8,
            recency_score=0.9,
            combined_score=0.85,
        )
        with pytest.raises(ValidationError):
            sm.combined_score = 0.5  # type: ignore[misc]

    def test_shared_flag(self) -> None:
        entry = _make_entry()
        sm = ScoredMemory(
            entry=entry,
            relevance_score=0.8,
            recency_score=0.9,
            combined_score=0.85,
            is_shared=True,
        )
        assert sm.is_shared is True

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("relevance_score", 1.5),
            ("relevance_score", -0.1),
            ("recency_score", 1.5),
            ("recency_score", -0.1),
            ("combined_score", 1.5),
            ("combined_score", -0.1),
        ],
    )
    def test_out_of_range_score_rejected(self, field: str, value: float) -> None:
        entry = _make_entry()
        kwargs = {
            "entry": entry,
            "relevance_score": 0.5,
            "recency_score": 0.5,
            "combined_score": 0.5,
            field: value,
        }
        with pytest.raises(ValidationError):
            ScoredMemory(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("relevance_score", float("nan")),
            ("relevance_score", float("inf")),
            ("recency_score", float("nan")),
            ("recency_score", float("-inf")),
            ("combined_score", float("nan")),
            ("combined_score", float("inf")),
        ],
    )
    def test_inf_nan_score_rejected(self, field: str, value: float) -> None:
        entry = _make_entry()
        kwargs = {
            "entry": entry,
            "relevance_score": 0.5,
            "recency_score": 0.5,
            "combined_score": 0.5,
            field: value,
        }
        with pytest.raises(ValidationError):
            ScoredMemory(**kwargs)  # type: ignore[arg-type]


# ── rank_memories ────────────────────────────────────────────────


@pytest.mark.unit
class TestRankMemories:
    def test_empty_input(self) -> None:
        config = MemoryRetrievalConfig()
        result = rank_memories((), config=config, now=datetime.now(UTC))
        assert result == ()

    def test_single_entry_above_threshold(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(relevance_score=0.8, created_at=now)
        config = MemoryRetrievalConfig(min_relevance=0.3)
        result = rank_memories((entry,), config=config, now=now)
        assert len(result) == 1
        assert result[0].entry is entry
        assert result[0].is_shared is False

    def test_single_entry_below_threshold_filtered(self) -> None:
        now = datetime.now(UTC)
        old = now - timedelta(hours=500)
        entry = _make_entry(relevance_score=0.1, created_at=old)
        config = MemoryRetrievalConfig(min_relevance=0.9)
        result = rank_memories((entry,), config=config, now=now)
        assert result == ()

    def test_sorted_by_combined_score_descending(self) -> None:
        now = datetime.now(UTC)
        high = _make_entry(
            entry_id="high",
            relevance_score=0.9,
            created_at=now,
        )
        low = _make_entry(
            entry_id="low",
            relevance_score=0.3,
            created_at=now - timedelta(hours=100),
        )
        config = MemoryRetrievalConfig(min_relevance=0.0)
        result = rank_memories((low, high), config=config, now=now)
        assert len(result) == 2
        assert result[0].entry.id == "high"
        assert result[1].entry.id == "low"
        assert result[0].combined_score >= result[1].combined_score

    def test_personal_boost_applied(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(relevance_score=0.5, created_at=now)
        config = MemoryRetrievalConfig(
            personal_boost=0.2,
            min_relevance=0.0,
        )
        result = rank_memories((entry,), config=config, now=now)
        # relevance = min(0.5 + 0.2, 1.0) = 0.7
        assert result[0].relevance_score == pytest.approx(0.7)

    def test_personal_boost_capped_at_one(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(relevance_score=0.95, created_at=now)
        config = MemoryRetrievalConfig(
            personal_boost=0.2,
            min_relevance=0.0,
        )
        result = rank_memories((entry,), config=config, now=now)
        assert result[0].relevance_score == 1.0

    def test_default_relevance_used_when_none(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(relevance_score=None, created_at=now)
        config = MemoryRetrievalConfig(
            default_relevance=0.6,
            personal_boost=0.0,
            min_relevance=0.0,
        )
        result = rank_memories((entry,), config=config, now=now)
        assert result[0].relevance_score == pytest.approx(0.6)

    def test_shared_entries_no_personal_boost(self) -> None:
        now = datetime.now(UTC)
        shared = _make_entry(
            entry_id="shared",
            relevance_score=0.5,
            created_at=now,
        )
        config = MemoryRetrievalConfig(
            personal_boost=0.3,
            min_relevance=0.0,
        )
        result = rank_memories(
            (),
            config=config,
            now=now,
            shared_entries=(shared,),
        )
        assert len(result) == 1
        assert result[0].is_shared is True
        # No personal boost for shared entries
        assert result[0].relevance_score == pytest.approx(0.5)

    def test_personal_and_shared_merged(self) -> None:
        now = datetime.now(UTC)
        personal = _make_entry(
            entry_id="personal",
            relevance_score=0.8,
            created_at=now,
        )
        shared = _make_entry(
            entry_id="shared",
            relevance_score=0.7,
            created_at=now,
        )
        config = MemoryRetrievalConfig(
            personal_boost=0.0,
            min_relevance=0.0,
        )
        result = rank_memories(
            (personal,),
            config=config,
            now=now,
            shared_entries=(shared,),
        )
        assert len(result) == 2
        # Higher relevance first
        assert result[0].entry.id == "personal"
        assert result[0].is_shared is False
        assert result[1].entry.id == "shared"
        assert result[1].is_shared is True

    def test_recency_affects_ranking(self) -> None:
        now = datetime.now(UTC)
        recent_low_relevance = _make_entry(
            entry_id="recent",
            relevance_score=0.4,
            created_at=now,
        )
        old_high_relevance = _make_entry(
            entry_id="old",
            relevance_score=0.6,
            created_at=now - timedelta(hours=200),
        )
        # Heavily weight recency
        config = MemoryRetrievalConfig(
            relevance_weight=0.3,
            recency_weight=0.7,
            min_relevance=0.0,
            personal_boost=0.0,
        )
        result = rank_memories(
            (old_high_relevance, recent_low_relevance),
            config=config,
            now=now,
        )
        # Recent entry should rank higher due to recency weighting
        assert result[0].entry.id == "recent"

    def test_max_memories_truncates_merged_result(self) -> None:
        """After merging personal + shared, result is capped at max_memories."""
        now = datetime.now(UTC)
        personal = tuple(
            _make_entry(
                entry_id=f"p{i}",
                relevance_score=0.8,
                created_at=now,
            )
            for i in range(5)
        )
        shared = tuple(
            _make_entry(
                entry_id=f"s{i}",
                relevance_score=0.7,
                created_at=now,
            )
            for i in range(5)
        )
        config = MemoryRetrievalConfig(
            max_memories=3,
            min_relevance=0.0,
            personal_boost=0.0,
        )
        result = rank_memories(
            personal,
            config=config,
            now=now,
            shared_entries=shared,
        )
        assert len(result) == 3

    def test_min_relevance_exact_boundary_included(self) -> None:
        """Entry whose combined_score equals min_relevance is included."""
        now = datetime.now(UTC)
        entry = _make_entry(relevance_score=0.5, created_at=now)
        config = MemoryRetrievalConfig(
            personal_boost=0.0,
            min_relevance=0.5,
            recency_decay_rate=0.0,  # recency = 1.0
            relevance_weight=1.0,
            recency_weight=0.0,
        )
        result = rank_memories((entry,), config=config, now=now)
        assert len(result) == 1
        assert result[0].combined_score == pytest.approx(0.5)

    def test_both_empty_returns_empty(self) -> None:
        config = MemoryRetrievalConfig()
        result = rank_memories(
            (),
            config=config,
            now=datetime.now(UTC),
            shared_entries=(),
        )
        assert result == ()
