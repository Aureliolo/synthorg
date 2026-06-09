"""Tests for memory ranking functions."""

import math
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.ranking import (
    FusionStrategy,
    ScoredMemory,
    compute_combined_score,
    compute_recency_score,
    rank_memories,
)
from synthorg.memory.ranking_rrf import fuse_ranked_lists
from synthorg.memory.retrieval_config import MemoryRetrievalConfig


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

    @pytest.mark.parametrize("hours", [1, 24, 72])
    def test_decay_matches_formula(self, hours: int) -> None:
        now = datetime.now(UTC)
        past = now - timedelta(hours=hours)
        score = compute_recency_score(past, now, decay_rate=0.01)
        expected = math.exp(-0.01 * hours)
        assert abs(score - expected) < 1e-9

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
    @pytest.mark.parametrize(
        ("relevance", "recency", "rel_w", "rec_w", "expected"),
        [
            (0.8, 0.6, 0.7, 0.3, 0.7 * 0.8 + 0.3 * 0.6),
            (0.9, 0.1, 1.0, 0.0, 0.9),
            (0.9, 0.1, 0.0, 1.0, 0.1),
            (0.8, 0.4, 0.5, 0.5, 0.6),
        ],
        ids=["default_weights", "all_relevance", "all_recency", "equal_weights"],
    )
    def test_weighted_combination(
        self,
        relevance: float,
        recency: float,
        rel_w: float,
        rec_w: float,
        expected: float,
    ) -> None:
        score = compute_combined_score(relevance, recency, rel_w, rec_w)
        assert abs(score - expected) < 1e-9


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


# ── FusionStrategy ──────────────────────────────────────────────


@pytest.mark.unit
class TestFusionStrategy:
    def test_linear_value(self) -> None:
        assert FusionStrategy.LINEAR.value == "linear"

    def test_rrf_value(self) -> None:
        assert FusionStrategy.RRF.value == "rrf"

    def test_is_str_enum(self) -> None:
        assert isinstance(FusionStrategy.LINEAR, str)
        assert isinstance(FusionStrategy.RRF, str)


# ── fuse_ranked_lists ───────────────────────────────────────────


@pytest.mark.unit
class TestFuseRankedLists:
    def test_empty_input(self) -> None:
        result = fuse_ranked_lists(())
        assert result == ()

    def test_all_empty_inner_lists(self) -> None:
        result = fuse_ranked_lists(((), ()))
        assert result == ()

    def test_single_list_preserves_order(self) -> None:
        now = datetime.now(UTC)
        a = _make_entry(entry_id="a", relevance_score=0.9, created_at=now)
        b = _make_entry(entry_id="b", relevance_score=0.5, created_at=now)
        result = fuse_ranked_lists(((a, b),))
        assert len(result) == 2
        assert result[0].entry.id == "a"
        assert result[1].entry.id == "b"

    def test_two_disjoint_lists(self) -> None:
        now = datetime.now(UTC)
        a = _make_entry(entry_id="a", created_at=now)
        b = _make_entry(entry_id="b", created_at=now)
        c = _make_entry(entry_id="c", created_at=now)
        d = _make_entry(entry_id="d", created_at=now)
        result = fuse_ranked_lists(((a, b), (c, d)))
        assert len(result) == 4
        ids = {r.entry.id for r in result}
        assert ids == {"a", "b", "c", "d"}
        # Rank-1 entries score higher than rank-2 entries
        rank1_ids = {"a", "c"}
        rank2_ids = {"b", "d"}
        rank1_scores = [r.combined_score for r in result if r.entry.id in rank1_ids]
        rank2_scores = [r.combined_score for r in result if r.entry.id in rank2_ids]
        assert min(rank1_scores) >= max(rank2_scores)

    def test_overlapping_entries_score_higher(self) -> None:
        """Entry appearing in both lists should rank above disjoint entries."""
        now = datetime.now(UTC)
        shared = _make_entry(entry_id="shared", created_at=now)
        only_a = _make_entry(entry_id="only-a", created_at=now)
        only_b = _make_entry(entry_id="only-b", created_at=now)
        list_a = (shared, only_a)
        list_b = (shared, only_b)
        result = fuse_ranked_lists((list_a, list_b))
        assert result[0].entry.id == "shared"
        assert result[0].combined_score > result[1].combined_score

    def test_rank1_in_all_lists_gets_max_score(self) -> None:
        now = datetime.now(UTC)
        top = _make_entry(entry_id="top", created_at=now)
        other = _make_entry(entry_id="other", created_at=now)
        result = fuse_ranked_lists(((top, other), (top, other)))
        assert result[0].entry.id == "top"
        assert result[0].combined_score == pytest.approx(1.0)

    def test_max_results_truncates(self) -> None:
        now = datetime.now(UTC)
        entries = tuple(
            _make_entry(entry_id=f"e{i}", created_at=now) for i in range(10)
        )
        result = fuse_ranked_lists((entries,), max_results=3)
        assert len(result) == 3

    def test_custom_k_preserves_ranking(self) -> None:
        """Different k values both produce correct ranking order."""
        now = datetime.now(UTC)
        a = _make_entry(entry_id="a", created_at=now)
        b = _make_entry(entry_id="b", created_at=now)
        result_small_k = fuse_ranked_lists(((a, b),), k=1)
        result_large_k = fuse_ranked_lists(((a, b),), k=1000)
        # Both k values should preserve rank ordering
        assert result_small_k[0].entry.id == "a"
        assert result_small_k[1].entry.id == "b"
        assert result_large_k[0].entry.id == "a"
        assert result_large_k[1].entry.id == "b"
        # Min-max normalization with 2 items gives 1.0 and 0.0
        assert result_small_k[0].combined_score == pytest.approx(1.0)
        assert result_small_k[1].combined_score == pytest.approx(0.0)
        assert result_large_k[0].combined_score == pytest.approx(1.0)
        assert result_large_k[1].combined_score == pytest.approx(0.0)

    def test_single_result_normalizes_to_one(self) -> None:
        now = datetime.now(UTC)
        a = _make_entry(entry_id="a", created_at=now)
        result = fuse_ranked_lists(((a,),))
        assert len(result) == 1
        assert result[0].combined_score == pytest.approx(1.0)

    def test_equal_raw_scores_normalize_to_one(self) -> None:
        """When all entries have the same raw RRF score, all get 1.0."""
        now = datetime.now(UTC)
        a = _make_entry(entry_id="a", created_at=now)
        b = _make_entry(entry_id="b", created_at=now)
        # Both at rank 1 in separate lists -- same raw score
        result = fuse_ranked_lists(((a,), (b,)))
        assert result[0].combined_score == pytest.approx(1.0)
        assert result[1].combined_score == pytest.approx(1.0)

    def test_relevance_score_preserves_raw(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(relevance_score=0.75, created_at=now)
        result = fuse_ranked_lists(((entry,),))
        assert result[0].relevance_score == pytest.approx(0.75)

    def test_relevance_score_defaults_to_zero_when_none(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(relevance_score=None, created_at=now)
        result = fuse_ranked_lists(((entry,),))
        assert result[0].relevance_score == pytest.approx(0.0)

    def test_recency_score_is_zero(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(created_at=now)
        result = fuse_ranked_lists(((entry,),))
        assert result[0].recency_score == pytest.approx(0.0)

    def test_is_shared_is_false(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(created_at=now)
        result = fuse_ranked_lists(((entry,),))
        assert result[0].is_shared is False

    def test_duplicate_id_across_lists_first_entry_wins(self) -> None:
        """When same ID appears in multiple lists, first MemoryEntry is kept."""
        now = datetime.now(UTC)
        first = _make_entry(entry_id="dup", relevance_score=0.9, created_at=now)
        second = _make_entry(entry_id="dup", relevance_score=0.1, created_at=now)
        result = fuse_ranked_lists(((first,), (second,)))
        assert len(result) == 1
        assert result[0].relevance_score == pytest.approx(0.9)

    def test_intra_list_duplicate_id_skipped(self) -> None:
        """Same ID within one list contributes only one rank score."""
        now = datetime.now(UTC)
        a = _make_entry(entry_id="a", created_at=now)
        a_dup = _make_entry(entry_id="a", created_at=now)
        b = _make_entry(entry_id="b", created_at=now)
        # List has [a, b, a_dup] -- a_dup at rank 3 should be skipped
        # a gets 1/(k+1), b gets 1/(k+2), a_dup is ignored
        result = fuse_ranked_lists(((a, b, a_dup),))
        assert len(result) == 2
        assert result[0].entry.id == "a"
        assert result[1].entry.id == "b"

    def test_exact_rrf_scores(self) -> None:
        """Verify exact normalized RRF scores with known k=60."""
        now = datetime.now(UTC)
        a = _make_entry(entry_id="a", created_at=now)
        b = _make_entry(entry_id="b", created_at=now)
        c = _make_entry(entry_id="c", created_at=now)
        # List 1: [a, b], List 2: [a, c]
        # Raw: a = 1/61 + 1/61 = 2/61, b = 1/62, c = 1/62
        # b and c have equal raw scores, a is highest
        # min = 1/62, max = 2/61
        # normalized(a) = (2/61 - 1/62) / (2/61 - 1/62) = 1.0
        # normalized(b) = (1/62 - 1/62) / (2/61 - 1/62) = 0.0
        # normalized(c) = 0.0
        result = fuse_ranked_lists(((a, b), (a, c)), k=60)
        assert result[0].entry.id == "a"
        assert result[0].combined_score == pytest.approx(1.0)
        assert {r.entry.id for r in result[1:]} == {"b", "c"}
        assert result[1].combined_score == pytest.approx(0.0)
        assert result[2].combined_score == pytest.approx(0.0)

    def test_three_lists_accumulation(self) -> None:
        """RRF scores accumulate correctly across 3+ lists."""
        now = datetime.now(UTC)
        a = _make_entry(entry_id="a", created_at=now)
        b = _make_entry(entry_id="b", created_at=now)
        c = _make_entry(entry_id="c", created_at=now)
        # a appears in all 3 lists at rank 1
        # b appears in 2 lists at rank 2
        # c appears in 1 list at rank 2
        result = fuse_ranked_lists(((a, b), (a, b), (a, c)), k=60)
        assert result[0].entry.id == "a"
        assert result[0].combined_score == pytest.approx(1.0)
        # b: 2 * 1/62 = 2/62, c: 1/62 -> b > c
        assert result[1].entry.id == "b"
        assert result[1].combined_score > result[2].combined_score
        assert result[2].entry.id == "c"

    def test_max_results_above_entry_count_returns_all(self) -> None:
        """When fewer entries exist than max_results, all are returned."""
        now = datetime.now(UTC)
        entries = tuple(_make_entry(entry_id=f"e{i}", created_at=now) for i in range(3))
        result = fuse_ranked_lists((entries,), max_results=20)
        assert len(result) == 3

    @pytest.mark.parametrize("bad_k", [0, -1, -100])
    def test_k_below_one_raises(self, bad_k: int) -> None:
        with pytest.raises(ValueError, match=r"k must be >= 1"):
            fuse_ranked_lists((), k=bad_k)

    @pytest.mark.parametrize("bad_max", [0, -1, -50])
    def test_max_results_below_one_raises(self, bad_max: int) -> None:
        with pytest.raises(ValueError, match=r"max_results must be >= 1"):
            fuse_ranked_lists((), max_results=bad_max)


# ── scoring_strategy field ────────────────────────────────────────


@pytest.mark.unit
class TestScoringStrategyField:
    def test_linear_scoring_strategy_set(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(relevance_score=0.8, created_at=now)
        config = MemoryRetrievalConfig(min_relevance=0.0)
        result = rank_memories((entry,), config=config, now=now)
        assert len(result) == 1
        assert result[0].scoring_strategy == FusionStrategy.LINEAR

    def test_rrf_scoring_strategy_set(self) -> None:
        now = datetime.now(UTC)
        entry = _make_entry(entry_id="a", created_at=now)
        result = fuse_ranked_lists(((entry,),))
        assert len(result) == 1
        assert result[0].scoring_strategy == FusionStrategy.RRF

    def test_scored_memory_default_strategy_none(self) -> None:
        entry = _make_entry()
        sm = ScoredMemory(
            entry=entry,
            relevance_score=0.5,
            recency_score=0.5,
            combined_score=0.5,
        )
        assert sm.scoring_strategy is None


# ── Diversity re-ranking ───────────────────────────────────────────
# Tests for ``bigram_jaccard`` and ``apply_diversity_penalty`` live in
# ``test_ranking_diversity.py``.
