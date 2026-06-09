"""CodSpeed benchmarks for the memory retrieval pipeline.

All targets are public, synchronous, and pure-compute -- ideal for CPU
Simulation. Each bench measures one entry point of the retrieval
pipeline end-to-end on representative input sizes.
"""

import pytest
from pytest_codspeed import BenchmarkFixture

from synthorg.memory.models import MemoryEntry
from synthorg.memory.ranking import rank_memories
from synthorg.memory.ranking_mmr import apply_diversity_penalty, bigram_jaccard
from synthorg.memory.ranking_rrf import fuse_ranked_lists
from tests.benchmarks._helpers import NOW, RETRIEVAL_CONFIG, make_memory_entry


@pytest.mark.benchmark
def test_rank_memories_100(
    benchmark: BenchmarkFixture,
    entries_100: tuple[MemoryEntry, ...],
) -> None:
    """Rank 100 personal memory entries (typical retrieval window)."""

    @benchmark
    def _() -> None:
        rank_memories(entries_100, config=RETRIEVAL_CONFIG, now=NOW)


@pytest.mark.benchmark
def test_rank_memories_1000(
    benchmark: BenchmarkFixture,
    entries_1000: tuple[MemoryEntry, ...],
) -> None:
    """Rank 1000 personal memory entries (large retrieval window)."""

    @benchmark
    def _() -> None:
        rank_memories(entries_1000, config=RETRIEVAL_CONFIG, now=NOW)


@pytest.mark.benchmark
def test_rank_memories_with_shared(
    benchmark: BenchmarkFixture,
    entries_100: tuple[MemoryEntry, ...],
    shared_entries_50: tuple[MemoryEntry, ...],
) -> None:
    """Rank 100 personal + 50 shared entries (exercises the merge path)."""

    @benchmark
    def _() -> None:
        rank_memories(
            entries_100,
            config=RETRIEVAL_CONFIG,
            now=NOW,
            shared_entries=shared_entries_50,
        )


@pytest.mark.benchmark
def test_fuse_ranked_lists_3x100(
    benchmark: BenchmarkFixture,
    entries_100: tuple[MemoryEntry, ...],
) -> None:
    """RRF fusion of 3 overlapping ranked lists of 100 entries."""
    list1 = entries_100
    list2 = entries_100[20:] + entries_100[:20]
    list3 = entries_100[50:] + entries_100[:50]
    ranked_lists = (list1, list2, list3)

    @benchmark
    def _() -> None:
        fuse_ranked_lists(ranked_lists, k=60, max_results=20)


@pytest.mark.benchmark
def test_fuse_ranked_lists_5x200(benchmark: BenchmarkFixture) -> None:
    """RRF fusion of 5 ranked lists of 200 entries each (large hybrid search)."""
    lists = tuple(
        tuple(
            make_memory_entry(
                src * 1000 + i,
                age_hours=i * 0.2,
                relevance=0.3 + (i % 15) * 0.04,
            )
            for i in range(200)
        )
        for src in range(5)
    )

    @benchmark
    def _() -> None:
        fuse_ranked_lists(lists, k=60, max_results=20)


@pytest.mark.benchmark
def test_bigram_jaccard_short(benchmark: BenchmarkFixture) -> None:
    """Bigram Jaccard similarity on short texts (~12 words each)."""
    text_a = "the quick brown fox jumps over the lazy dog near the river bank"
    text_b = "the quick red fox leaps over the lazy cat near the river bank"

    @benchmark
    def _() -> None:
        bigram_jaccard(text_a, text_b)


@pytest.mark.benchmark
def test_bigram_jaccard_long(benchmark: BenchmarkFixture) -> None:
    """Bigram Jaccard similarity on longer texts (~100 words each)."""
    words_a = " ".join(f"word{i % 50}" for i in range(100))
    words_b = " ".join(f"word{(i + 5) % 50}" for i in range(100))

    @benchmark
    def _() -> None:
        bigram_jaccard(words_a, words_b)


@pytest.mark.benchmark
def test_diversity_penalty_50(benchmark: BenchmarkFixture) -> None:
    """MMR diversity re-ranking on 50 scored memories (O(n^2k) pairwise sim)."""
    entries = tuple(
        make_memory_entry(
            i,
            age_hours=i * 0.3,
            relevance=0.5 + (i % 10) * 0.05,
            content=(
                f"Topic {i % 5}: detailed analysis of subject {i} "
                "with various technical terms and specific domain vocabulary "
                f"covering aspect {i % 3} of the problem space"
            ),
        )
        for i in range(50)
    )
    scored = rank_memories(entries, config=RETRIEVAL_CONFIG, now=NOW)

    @benchmark
    def _() -> None:
        apply_diversity_penalty(scored, diversity_lambda=0.7)
