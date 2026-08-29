"""Tests for memory ranking diversity utilities.

Covers word-bigram Jaccard similarity and the MMR-based
``apply_diversity_penalty`` re-ranker.
"""

import math
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.ranking import ScoredMemory
from synthorg.memory.ranking_mmr import apply_diversity_penalty, bigram_jaccard


def _make_entry(
    *,
    entry_id: str = "mem-1",
    content: str = "test memory",
) -> MemoryEntry:
    """Helper to build a MemoryEntry with sensible defaults."""
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        category=MemoryCategory.EPISODIC,
        content=content,
        metadata=MemoryMetadata(),
        created_at=datetime.now(UTC),
    )


def _make_scored(
    *,
    entry_id: str = "mem-1",
    content: str = "test memory content",
    combined_score: float = 0.8,
    relevance_score: float = 0.8,
) -> ScoredMemory:
    """Helper to build a ScoredMemory with sensible defaults."""
    entry = _make_entry(entry_id=entry_id, content=content)
    return ScoredMemory(
        entry=entry,
        relevance_score=relevance_score,
        recency_score=0.5,
        combined_score=combined_score,
    )


# ── bigram_jaccard ────────────────────────────────────────────────


@pytest.mark.unit
class TestBigramJaccard:
    def test_identical_strings_return_one(self) -> None:
        assert bigram_jaccard("foo bar baz", "foo bar baz") == 1.0

    def test_completely_different_strings_return_zero(self) -> None:
        assert bigram_jaccard("alpha beta gamma", "delta epsilon zeta") == 0.0

    def test_empty_string_returns_zero(self) -> None:
        assert bigram_jaccard("", "hello world") == 0.0
        assert bigram_jaccard("hello world", "") == 0.0
        assert bigram_jaccard("", "") == 0.0

    def test_single_word_returns_zero(self) -> None:
        """No bigrams possible with a single word."""
        assert bigram_jaccard("hello", "hello") == 0.0
        assert bigram_jaccard("hello", "world") == 0.0

    def test_partial_overlap(self) -> None:
        # "a b c" bigrams: {(a,b), (b,c)}
        # "a b d" bigrams: {(a,b), (b,d)}
        # intersection: {(a,b)}, union: {(a,b), (b,c), (b,d)}
        score = bigram_jaccard("a b c", "a b d")
        assert score == pytest.approx(1.0 / 3.0)

    def test_case_insensitive(self) -> None:
        assert bigram_jaccard("Hello World", "hello world") == 1.0


# ── apply_diversity_penalty ────────────────────────────────────────


@pytest.mark.unit
class TestApplyDiversityPenalty:
    def test_empty_input_returns_empty(self) -> None:
        result = apply_diversity_penalty(())
        assert result == ()

    def test_single_entry_returns_unchanged(self) -> None:
        sm = _make_scored(entry_id="a")
        result = apply_diversity_penalty((sm,))
        assert result == (sm,)

    def test_identical_content_second_penalized(self) -> None:
        """Two entries with identical content: first by relevance wins."""
        high = _make_scored(
            entry_id="high",
            content="the same content here",
            combined_score=0.9,
        )
        low = _make_scored(
            entry_id="low",
            content="the same content here",
            combined_score=0.8,
        )
        result = apply_diversity_penalty((high, low), diversity_lambda=0.5)
        assert result[0].entry.id == "high"
        assert result[1].entry.id == "low"

    def test_distinct_content_ordering_preserved(self) -> None:
        """Two entries with completely different content keep relevance order."""
        high = _make_scored(
            entry_id="high",
            content="alpha beta gamma delta",
            combined_score=0.9,
        )
        low = _make_scored(
            entry_id="low",
            content="epsilon zeta eta theta",
            combined_score=0.8,
        )
        result = apply_diversity_penalty((high, low), diversity_lambda=0.7)
        assert result[0].entry.id == "high"
        assert result[1].entry.id == "low"

    def test_lambda_one_pure_relevance(self) -> None:
        """lambda=1.0 means no diversity penalty -- pure relevance order."""
        high = _make_scored(
            entry_id="high",
            content="the same content here now",
            combined_score=0.9,
        )
        low = _make_scored(
            entry_id="low",
            content="the same content here now",
            combined_score=0.8,
        )
        result = apply_diversity_penalty(
            (high, low),
            diversity_lambda=1.0,
        )
        assert result[0].entry.id == "high"
        assert result[1].entry.id == "low"

    def test_lambda_zero_maximum_diversity(self) -> None:
        """lambda=0.0 maximizes diversity -- dissimilar entry selected second."""
        similar = _make_scored(
            entry_id="similar",
            content="the cat sat on the mat",
            combined_score=0.8,
        )
        duplicate = _make_scored(
            entry_id="duplicate",
            content="the cat sat on the mat",
            combined_score=0.85,
        )
        different = _make_scored(
            entry_id="different",
            content="alpha beta gamma delta epsilon",
            combined_score=0.7,
        )
        # With lambda=0, after selecting the highest-scored first entry,
        # the next pick maximizes dissimilarity to the selected set.
        result = apply_diversity_penalty(
            (duplicate, similar, different),
            diversity_lambda=0.0,
        )
        # First is duplicate (highest score, no penalty on first pick)
        # Second should be "different" (most dissimilar to "duplicate")
        assert result[0].entry.id == "duplicate"
        assert result[1].entry.id == "different"

    def test_custom_similarity_fn_used(self) -> None:
        """Custom similarity function is actually consulted by MMR.

        For single-word inputs the default bigram Jaccard is ``0.0``,
        which means an override that also returns ``0.0`` would pass
        whether it was called or not -- a false positive.  This test
        uses a spy that records calls and returns a distinguishing
        value, and then asserts on both the call record and the
        reordering effect.
        """
        a = _make_scored(entry_id="a", content="x", combined_score=0.9)
        b = _make_scored(entry_id="b", content="y", combined_score=0.89)
        c = _make_scored(entry_id="c", content="z", combined_score=0.88)

        call_log: list[tuple[str, str]] = []

        def _spy_similarity(left: str, right: str) -> float:
            call_log.append((left, right))
            # Make 'b' look like a near-duplicate of 'a' (similarity
            # 0.99) and 'c' completely orthogonal (similarity 0.0).
            # With lambda=0.3 this pushes 'c' ahead of 'b' despite
            # lower combined_score.
            if {left, right} == {"x", "y"}:
                return 0.99
            return 0.0

        result = apply_diversity_penalty(
            (a, b, c),
            diversity_lambda=0.3,
            similarity_fn=_spy_similarity,
        )
        assert len(result) == 3
        assert call_log, "custom similarity_fn was never invoked"
        ids = [r.entry.id for r in result]
        assert ids[0] == "a", f"'a' should be picked first (highest score): {ids}"
        assert ids.index("c") < ids.index("b"), (
            f"MMR should prefer the diverse 'c' over the near-duplicate 'b' "
            f"when similarity_fn treats them as highly similar: {ids}"
        )

    def test_result_length_equals_input(self) -> None:
        entries = tuple(
            _make_scored(
                entry_id=f"e{i}",
                content=f"content number {i} is unique enough",
                combined_score=0.9 - i * 0.1,
            )
            for i in range(5)
        )
        result = apply_diversity_penalty(entries, diversity_lambda=0.5)
        assert len(result) == len(entries)

    @pytest.mark.parametrize(
        "bad_lambda",
        [
            -0.1,
            1.1,
            -1.0,
            2.0,
            float("nan"),
            float("inf"),
            -float("inf"),
        ],
    )
    def test_invalid_lambda_raises(self, bad_lambda: float) -> None:
        with pytest.raises(ValueError, match=r"diversity_lambda must be"):
            apply_diversity_penalty((), diversity_lambda=bad_lambda)

    def test_diversity_promotes_coverage(self) -> None:
        """With moderate lambda, a diverse entry beats a redundant one."""
        base = _make_scored(
            entry_id="base",
            content="the project uses litestar framework for web",
            combined_score=0.95,
        )
        redundant = _make_scored(
            entry_id="redundant",
            content="the project uses litestar framework for api",
            combined_score=0.9,
        )
        diverse = _make_scored(
            entry_id="diverse",
            content="deployment runs on docker with nginx proxy",
            combined_score=0.85,
        )
        result = apply_diversity_penalty(
            (base, redundant, diverse),
            diversity_lambda=0.3,
        )
        assert result[0].entry.id == "base"
        # "diverse" should beat "redundant" due to diversity
        assert result[1].entry.id == "diverse"
        assert result[2].entry.id == "redundant"

    def test_all_negative_mmr_returns_all_entries_deterministically(self) -> None:
        """Lambda=0.0 with identical content: MMR sentinel must not wedge.

        With ``diversity_lambda=0.0`` and identical content across all
        entries, every MMR score is ``-1.0``.  The sentinel must be
        ``-math.inf`` (not ``-1.0``) so the first candidate is always
        selected deterministically rather than the loop jamming on
        ``best_idx = 0`` by accident.
        """
        entries = tuple(
            _make_scored(
                entry_id=f"e{i}",
                content="identical text content here",
                combined_score=0.0,
            )
            for i in range(3)
        )
        result = apply_diversity_penalty(entries, diversity_lambda=0.0)
        # All entries present in input order.
        assert len(result) == 3
        assert [r.entry.id for r in result] == ["e0", "e1", "e2"]


def _rerank_recomputing_every_pass(
    scored: tuple[ScoredMemory, ...],
    *,
    diversity_lambda: float,
    similarity_fn: Callable[[str, str], float],
) -> tuple[str, ...]:
    """Reference MMR that recomputes the max over all selected each pass.

    The textbook statement of the algorithm, kept here as an oracle: the
    production re-ranker folds each selection into a running maximum
    instead, and the two must agree exactly.

    Returns:
        The re-ranked entry ids.
    """
    remaining = list(scored)
    selected: list[ScoredMemory] = []
    while remaining:
        best_idx = 0
        best_mmr = -math.inf
        for i, candidate in enumerate(remaining):
            relevance = diversity_lambda * candidate.combined_score
            max_sim = (
                max(
                    similarity_fn(candidate.entry.content, s.entry.content)
                    for s in selected
                )
                if selected
                else 0.0
            )
            mmr = relevance - (1.0 - diversity_lambda) * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        selected.append(remaining.pop(best_idx))
    return tuple(s.entry.id for s in selected)


@pytest.mark.unit
class TestDiversityPenaltyPairwiseCost:
    """MMR compares each pair once, which is what keeps it quadratic.

    Recomputing the maximum over every selected entry on each pass costs
    ``sum (n - s) * s`` comparisons, cubic in the input, and the
    ``docs/design/memory-learning.md`` complexity claim is ``O(n**2)``.
    Pinning the count is what stops the cubic shape returning unnoticed:
    it produces identical output, so no behavioural test can see it.
    """

    @staticmethod
    def _cubic_call_count(n: int) -> int:
        """Comparisons the recompute-every-pass formulation would make.

        Returns:
            ``sum over s of (n - s) * s`` for ``s`` in ``0..n-1``.
        """
        return sum((n - s) * s for s in range(n))

    @pytest.mark.parametrize("size", [2, 5, 12])
    def test_custom_similarity_fn_called_once_per_pair(self, size: int) -> None:
        entries = tuple(
            _make_scored(
                entry_id=f"e{i}",
                content=f"entry {i} body",
                combined_score=1.0 - i * 0.01,
            )
            for i in range(size)
        )
        calls = 0

        def _counting_similarity(left: str, right: str) -> float:
            nonlocal calls
            calls += 1
            return 0.5 if left != right else 1.0

        apply_diversity_penalty(
            entries,
            diversity_lambda=0.7,
            similarity_fn=_counting_similarity,
        )

        assert calls == size * (size - 1) // 2
        # The shape being guarded against, not merely a smaller number.
        if size > 2:
            assert calls < self._cubic_call_count(size)

    def test_bigram_path_compares_each_pair_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default path is counted at its own similarity primitive."""
        from synthorg.memory import ranking_mmr

        size = 12
        entries = tuple(
            _make_scored(
                entry_id=f"e{i}",
                content=f"topic {i % 3} detailed body text for entry {i}",
                combined_score=1.0 - i * 0.01,
            )
            for i in range(size)
        )
        real = ranking_mmr._bigram_jaccard_sets
        calls = 0

        def _counting(
            bigrams_a: frozenset[tuple[str, str]],
            bigrams_b: frozenset[tuple[str, str]],
        ) -> float:
            nonlocal calls
            calls += 1
            return real(bigrams_a, bigrams_b)

        monkeypatch.setattr(ranking_mmr, "_bigram_jaccard_sets", _counting)
        apply_diversity_penalty(entries, diversity_lambda=0.7)

        assert calls == size * (size - 1) // 2
        assert calls < self._cubic_call_count(size)


@pytest.mark.unit
class TestDiversityPenaltyProperties:
    """The running maximum must agree with recomputing it every pass."""

    @given(
        scores=st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
            min_size=2,
            max_size=8,
        ),
        similarities=st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
            min_size=1,
            max_size=40,
        ),
        diversity_lambda=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    def test_matches_the_recomputing_reference(
        self,
        scores: list[float],
        similarities: list[float],
        diversity_lambda: float,
    ) -> None:
        entries = tuple(
            _make_scored(entry_id=f"e{i}", content=f"c{i}", combined_score=score)
            for i, score in enumerate(scores)
        )

        def _table_similarity(left: str, right: str) -> float:
            # Symmetric and deterministic, as MMR assumes, but otherwise
            # arbitrary so the ordering is not accidentally monotonic.
            key = hash(frozenset((left, right))) % len(similarities)
            return similarities[key]

        result = apply_diversity_penalty(
            entries,
            diversity_lambda=diversity_lambda,
            similarity_fn=_table_similarity,
        )
        expected = _rerank_recomputing_every_pass(
            entries,
            diversity_lambda=diversity_lambda,
            similarity_fn=_table_similarity,
        )

        assert tuple(r.entry.id for r in result) == expected
