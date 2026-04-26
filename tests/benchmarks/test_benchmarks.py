"""CodSpeed performance benchmarks for synthorg core hot paths.

Benchmarks cover:
- Memory ranking and RRF fusion (retrieval pipeline)
- Budget cost aggregation and anomaly detection
- Coordination metrics computation
- Agent-task routing scorer
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from synthorg.budget._aggregation import (
    compute_cost_per_1k,
    group_by_agent,
    sum_cost,
    sum_tokens,
)
from synthorg.budget._optimizer_helpers import (
    _classify_severity,
    _compute_window_costs,
    _rate_efficiency,
)
from synthorg.budget.coordination_metrics import (
    compute_amdahl_ceiling,
    compute_efficiency,
    compute_message_overhead,
    compute_redundancy_rate,
    compute_straggler_gap,
    compute_token_speedup_ratio,
)
from synthorg.budget.cost_record import CostRecord
from synthorg.core.enums import (
    AgentStatus,
    Complexity,
    MemoryCategory,
    SeniorityLevel,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.ranking import (
    apply_diversity_penalty,
    bigram_jaccard,
    fuse_ranked_lists,
    rank_memories,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig

# ---------------------------------------------------------------------------
# Fixtures: reusable test data builders
# ---------------------------------------------------------------------------


def _make_memory_entry(
    idx: int,
    *,
    age_hours: float = 0.0,
    relevance: float = 0.8,
    content: str | None = None,
) -> MemoryEntry:
    """Build a MemoryEntry with deterministic fields."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    created = now - timedelta(hours=age_hours)
    return MemoryEntry(
        id=f"mem-{idx:04d}",
        agent_id="agent-bench",
        category=MemoryCategory.EPISODIC,
        content=content or f"Memory entry number {idx} with some benchmark content",
        metadata=MemoryMetadata(
            source="benchmark",
            confidence=0.9,
            tags=("bench",),
        ),
        created_at=created,
        relevance_score=relevance,
    )


def _make_cost_record(  # noqa: PLR0913
    idx: int,
    *,
    agent_id: str = "agent-1",
    cost: float = 0.05,
    input_tokens: int = 500,
    output_tokens: int = 200,
    hours_ago: float = 0.0,
) -> CostRecord:
    """Build a CostRecord with deterministic fields."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ts = now - timedelta(hours=hours_ago)
    return CostRecord(
        agent_id=agent_id,
        task_id=f"task-{idx:04d}",
        provider="benchmark-provider",
        model="bench-model-001",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        currency="USD",
        timestamp=ts,
    )


_RETRIEVAL_CONFIG = MemoryRetrievalConfig(
    relevance_weight=0.7,
    recency_weight=0.3,
    recency_decay_rate=0.01,
    personal_boost=0.1,
    min_relevance=0.3,
    max_memories=20,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Memory ranking benchmarks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def entries_100() -> tuple[MemoryEntry, ...]:
    return tuple(
        _make_memory_entry(i, age_hours=i * 0.5, relevance=0.5 + (i % 10) * 0.05)
        for i in range(100)
    )


@pytest.fixture(scope="module")
def entries_1000() -> tuple[MemoryEntry, ...]:
    return tuple(
        _make_memory_entry(i, age_hours=i * 0.1, relevance=0.4 + (i % 20) * 0.03)
        for i in range(1000)
    )


@pytest.fixture(scope="module")
def shared_entries_50() -> tuple[MemoryEntry, ...]:
    return tuple(
        _make_memory_entry(
            5000 + i,
            age_hours=i * 1.0,
            relevance=0.6 + (i % 8) * 0.05,
        )
        for i in range(50)
    )


@pytest.mark.benchmark
def test_rank_memories_100(
    benchmark: pytest.BenchmarkFixture,
    entries_100: tuple[MemoryEntry, ...],
) -> None:
    """Rank 100 personal memory entries."""

    @benchmark
    def _() -> None:
        rank_memories(entries_100, config=_RETRIEVAL_CONFIG, now=_NOW)


@pytest.mark.benchmark
def test_rank_memories_1000(
    benchmark: pytest.BenchmarkFixture,
    entries_1000: tuple[MemoryEntry, ...],
) -> None:
    """Rank 1000 personal memory entries."""

    @benchmark
    def _() -> None:
        rank_memories(entries_1000, config=_RETRIEVAL_CONFIG, now=_NOW)


@pytest.mark.benchmark
def test_rank_memories_with_shared(
    benchmark: pytest.BenchmarkFixture,
    entries_100: tuple[MemoryEntry, ...],
    shared_entries_50: tuple[MemoryEntry, ...],
) -> None:
    """Rank 100 personal + 50 shared entries (merge path)."""

    @benchmark
    def _() -> None:
        rank_memories(
            entries_100,
            config=_RETRIEVAL_CONFIG,
            now=_NOW,
            shared_entries=shared_entries_50,
        )


@pytest.mark.benchmark
def test_fuse_ranked_lists_3x100(
    benchmark: pytest.BenchmarkFixture,
    entries_100: tuple[MemoryEntry, ...],
) -> None:
    """RRF fusion of 3 overlapping ranked lists of 100 entries."""
    # Simulate 3 retrieval sources with overlapping results
    list1 = entries_100
    list2 = entries_100[20:] + entries_100[:20]
    list3 = entries_100[50:] + entries_100[:50]
    ranked_lists = (list1, list2, list3)

    @benchmark
    def _() -> None:
        fuse_ranked_lists(ranked_lists, k=60, max_results=20)


@pytest.mark.benchmark
def test_fuse_ranked_lists_5x200(
    benchmark: pytest.BenchmarkFixture,
) -> None:
    """RRF fusion of 5 ranked lists of 200 entries each."""
    lists = tuple(
        tuple(
            _make_memory_entry(
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
def test_bigram_jaccard_short(benchmark: pytest.BenchmarkFixture) -> None:
    """Bigram Jaccard similarity on short texts (~20 words)."""
    text_a = "the quick brown fox jumps over the lazy dog near the river bank"
    text_b = "the quick red fox leaps over the lazy cat near the river bank"

    @benchmark
    def _() -> None:
        bigram_jaccard(text_a, text_b)


@pytest.mark.benchmark
def test_bigram_jaccard_long(benchmark: pytest.BenchmarkFixture) -> None:
    """Bigram Jaccard similarity on longer texts (~100 words)."""
    words_a = " ".join(f"word{i % 50}" for i in range(100))
    words_b = " ".join(f"word{(i + 5) % 50}" for i in range(100))

    @benchmark
    def _() -> None:
        bigram_jaccard(words_a, words_b)


@pytest.mark.benchmark
def test_diversity_penalty_50(benchmark: pytest.BenchmarkFixture) -> None:
    """MMR diversity re-ranking on 50 scored memories."""
    config = _RETRIEVAL_CONFIG
    entries = tuple(
        _make_memory_entry(
            i,
            age_hours=i * 0.3,
            relevance=0.5 + (i % 10) * 0.05,
            content=f"Topic {i % 5}: detailed analysis of subject {i} "
            f"with various technical terms and specific domain vocabulary "
            f"covering aspect {i % 3} of the problem space",
        )
        for i in range(50)
    )
    scored = rank_memories(entries, config=config, now=_NOW)

    @benchmark
    def _() -> None:
        apply_diversity_penalty(scored, diversity_lambda=0.7)


# ---------------------------------------------------------------------------
# Budget aggregation benchmarks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cost_records_500() -> list[CostRecord]:
    agents = [f"agent-{a}" for a in range(10)]
    return [
        _make_cost_record(
            i,
            agent_id=agents[i % 10],
            cost=0.01 + (i % 20) * 0.005,
            input_tokens=100 + i * 10,
            output_tokens=50 + i * 5,
            hours_ago=i * 0.5,
        )
        for i in range(500)
    ]


@pytest.fixture(scope="module")
def cost_records_2000() -> list[CostRecord]:
    agents = [f"agent-{a}" for a in range(20)]
    return [
        _make_cost_record(
            i,
            agent_id=agents[i % 20],
            cost=0.02 + (i % 30) * 0.003,
            input_tokens=200 + i * 5,
            output_tokens=80 + i * 3,
            hours_ago=i * 0.25,
        )
        for i in range(2000)
    ]


@pytest.mark.benchmark
def test_group_by_agent_500(
    benchmark: pytest.BenchmarkFixture,
    cost_records_500: list[CostRecord],
) -> None:
    """Group 500 cost records by agent (10 agents)."""

    @benchmark
    def _() -> None:
        group_by_agent(cost_records_500)


@pytest.mark.benchmark
def test_sum_cost_2000(
    benchmark: pytest.BenchmarkFixture,
    cost_records_2000: list[CostRecord],
) -> None:
    """Sum cost across 2000 records (math.fsum precision)."""

    @benchmark
    def _() -> None:
        sum_cost(cost_records_2000)


@pytest.mark.benchmark
def test_sum_tokens_2000(
    benchmark: pytest.BenchmarkFixture,
    cost_records_2000: list[CostRecord],
) -> None:
    """Sum tokens across 2000 records."""

    @benchmark
    def _() -> None:
        sum_tokens(cost_records_2000)


@pytest.mark.benchmark
def test_compute_cost_per_1k(benchmark: pytest.BenchmarkFixture) -> None:
    """Cost per 1k tokens computation."""

    @benchmark
    def _() -> None:
        compute_cost_per_1k(125.50, 2_500_000)


@pytest.mark.benchmark
def test_compute_window_costs(
    benchmark: pytest.BenchmarkFixture,
    cost_records_500: list[CostRecord],
) -> None:
    """Compute per-window costs across 12 windows for one agent."""
    agent_records = [r for r in cost_records_500 if r.agent_id == "agent-0"]
    window_duration = timedelta(hours=24)
    window_starts = tuple(
        datetime(2025, 12, 20, tzinfo=UTC) + timedelta(days=d) for d in range(12)
    )

    @benchmark
    def _() -> None:
        _compute_window_costs(agent_records, window_starts, window_duration)


# ---------------------------------------------------------------------------
# Optimizer helper benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_classify_severity(benchmark: pytest.BenchmarkFixture) -> None:
    """Severity classification for anomaly detection."""
    values = [0.5, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

    @benchmark
    def _() -> None:
        for v in values:
            _classify_severity(v)


@pytest.mark.benchmark
def test_rate_efficiency(benchmark: pytest.BenchmarkFixture) -> None:
    """Efficiency rating relative to global average."""
    cases = [
        (0.05, 0.10, 1.5, 0.5),  # efficient
        (0.10, 0.10, 1.5, 0.5),  # normal
        (0.20, 0.10, 1.5, 0.5),  # inefficient
        (0.00, 0.00, 1.5, 0.5),  # zero avg
    ]

    @benchmark
    def _() -> None:
        for cost, avg, thresh, lower in cases:
            _rate_efficiency(cost, avg, thresh, lower)


# ---------------------------------------------------------------------------
# Coordination metrics benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_compute_efficiency(benchmark: pytest.BenchmarkFixture) -> None:
    """Coordination efficiency metric."""

    @benchmark
    def _() -> None:
        compute_efficiency(success_rate=0.85, turns_mas=12.0, turns_sas=8.0)


@pytest.mark.benchmark
def test_compute_amdahl_ceiling(benchmark: pytest.BenchmarkFixture) -> None:
    """Amdahl's Law ceiling with recommended team size."""

    @benchmark
    def _() -> None:
        compute_amdahl_ceiling(parallelizable_fraction=0.8)


@pytest.mark.benchmark
def test_compute_straggler_gap(benchmark: pytest.BenchmarkFixture) -> None:
    """Straggler gap across 20 agents."""
    durations = [(f"agent-{i}", 10.0 + i * 2.5) for i in range(20)]

    @benchmark
    def _() -> None:
        compute_straggler_gap(agent_durations=durations)


@pytest.mark.benchmark
def test_compute_redundancy_rate(benchmark: pytest.BenchmarkFixture) -> None:
    """Redundancy rate from 100 similarity samples."""
    similarities = [0.1 + (i % 9) * 0.1 for i in range(100)]

    @benchmark
    def _() -> None:
        compute_redundancy_rate(similarities=similarities)


@pytest.mark.benchmark
def test_compute_token_speedup_ratio(benchmark: pytest.BenchmarkFixture) -> None:
    """Token/speedup ratio alert check."""

    @benchmark
    def _() -> None:
        compute_token_speedup_ratio(
            tokens_mas=50000.0,
            tokens_sas=20000.0,
            duration_mas=30.0,
            duration_sas=60.0,
        )


@pytest.mark.benchmark
def test_compute_message_overhead(benchmark: pytest.BenchmarkFixture) -> None:
    """O(n^2) message overhead detection."""

    @benchmark
    def _() -> None:
        compute_message_overhead(
            team_size=10,
            message_count=75,
            quadratic_threshold=0.5,
        )


# ---------------------------------------------------------------------------
# Task routing scorer benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_agent_task_scorer(benchmark: pytest.BenchmarkFixture) -> None:
    """Score a single agent against a subtask (full scoring path)."""
    from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
    from synthorg.core.role import Skill
    from synthorg.engine.decomposition.models import SubtaskDefinition
    from synthorg.engine.routing.scorer import AgentTaskScorer

    skills = tuple(
        Skill(
            id=f"skill-{i}",
            name=f"Skill {i}",
            tags=(f"tag-{i % 3}",),
            proficiency=0.7 + (i % 4) * 0.1,
        )
        for i in range(5)
    )
    agent = AgentIdentity(
        name="Benchmark Agent",
        role="backend developer",
        department="engineering",
        level=SeniorityLevel.SENIOR,
        status=AgentStatus.ACTIVE,
        skills=SkillSet(primary=skills[:3], secondary=skills[3:]),
        model=ModelConfig(provider="test", model_id="test-model"),
        hiring_date=date(2025, 1, 1),
    )
    subtask = SubtaskDefinition(
        id="bench-subtask",
        title="Benchmark Task",
        description="A benchmark subtask for scoring evaluation",
        estimated_complexity=Complexity.COMPLEX,
        required_skills=("skill-0", "skill-1", "skill-3"),
        required_tags=("tag-0",),
        required_role="backend developer",
    )
    scorer = AgentTaskScorer(min_score=0.1)

    @benchmark
    def _() -> None:
        scorer.score(agent, subtask)


@pytest.mark.benchmark
def test_agent_task_scorer_10_agents(benchmark: pytest.BenchmarkFixture) -> None:
    """Score 10 agents against a subtask (batch routing)."""
    from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
    from synthorg.core.role import Skill
    from synthorg.engine.decomposition.models import SubtaskDefinition
    from synthorg.engine.routing.scorer import AgentTaskScorer

    agents = []
    levels = list(SeniorityLevel)
    for a in range(10):
        skills = tuple(
            Skill(
                id=f"skill-{s}",
                name=f"Skill {s}",
                tags=(f"tag-{s % 3}",),
                proficiency=0.6 + (s % 5) * 0.08,
            )
            for s in range(a % 4 + 2)
        )
        agents.append(
            AgentIdentity(
                name=f"Agent {a}",
                role="backend developer" if a % 3 == 0 else "frontend developer",
                department="engineering",
                level=levels[a % len(levels)],
                status=AgentStatus.ACTIVE,
                skills=SkillSet(
                    primary=skills[: len(skills) // 2],
                    secondary=skills[len(skills) // 2 :],
                ),
                model=ModelConfig(provider="test", model_id="test-model"),
                hiring_date=date(2025, 1, 1),
            )
        )

    subtask = SubtaskDefinition(
        id="bench-subtask",
        title="Benchmark Task",
        description="A benchmark subtask for batch scoring evaluation",
        estimated_complexity=Complexity.COMPLEX,
        required_skills=("skill-0", "skill-1", "skill-2"),
        required_tags=("tag-0", "tag-1"),
        required_role="backend developer",
    )
    scorer = AgentTaskScorer(min_score=0.1)

    @benchmark
    def _() -> None:
        for agent in agents:
            scorer.score(agent, subtask)
