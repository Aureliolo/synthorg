"""Performance benchmarks for SynthOrg core subsystems.

These benchmarks exercise CPU-bound hot paths across memory ranking,
DAG analysis, agent-task scoring, and coordination metrics.  They
are designed to run under CodSpeed's simulation instrument for
reproducible, hardware-agnostic measurement.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from synthorg.budget.coordination_metrics import (
    compute_amdahl_ceiling,
    compute_efficiency,
    compute_message_overhead,
    compute_overhead,
    compute_straggler_gap,
    compute_token_speedup_ratio,
)
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import (
    AgentStatus,
    Complexity,
    MemoryCategory,
    SeniorityLevel,
)
from synthorg.core.role import Skill
from synthorg.engine.decomposition.dag import DependencyGraph
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.ranking import (
    apply_diversity_penalty,
    fuse_ranked_lists,
    rank_memories,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig

# ---------------------------------------------------------------------------
# Fixtures: reusable test data builders
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
_BASE_DATE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

_DEFAULT_CONTENT = (
    "Memory entry number {idx} with some representative text content for benchmarking"
)


def _make_memory_entry(
    idx: int,
    *,
    hours_ago: float = 0.0,
    relevance: float = 0.8,
    content: str = "",
) -> MemoryEntry:
    """Build a minimal MemoryEntry for benchmarks."""
    created = _NOW - timedelta(hours=hours_ago)
    return MemoryEntry(
        id=f"mem-{idx:04d}",
        agent_id="agent-bench",
        category=MemoryCategory.EPISODIC,
        content=content or _DEFAULT_CONTENT.format(idx=idx),
        metadata=MemoryMetadata(source="benchmark"),
        created_at=created,
        relevance_score=relevance,
    )


def _make_retrieval_config() -> MemoryRetrievalConfig:
    """Build a default retrieval config for benchmarks."""
    return MemoryRetrievalConfig(
        relevance_weight=0.7,
        recency_weight=0.3,
        recency_decay_rate=0.01,
        personal_boost=0.1,
        min_relevance=0.2,
        max_memories=50,
    )


def _make_subtask(
    idx: int,
    dependencies: tuple[str, ...] = (),
) -> SubtaskDefinition:
    """Build a SubtaskDefinition for DAG benchmarks."""
    return SubtaskDefinition(
        id=f"subtask-{idx:03d}",
        title=f"Subtask {idx}",
        description=f"Description for subtask {idx}",
        dependencies=dependencies,
        estimated_complexity=Complexity.MEDIUM,
        required_skills=("python", "testing"),
    )


def _make_agent(
    idx: int,
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
    role: str = "engineer",
    skills: tuple[str, ...] = ("python", "testing"),
) -> AgentIdentity:
    """Build a minimal AgentIdentity for routing benchmarks."""
    primary = tuple(Skill(id=sid, name=sid.title()) for sid in skills)
    return AgentIdentity(
        name=f"Agent-{idx:03d}",
        role=role,
        department="Engineering",
        level=level,
        skills=SkillSet(primary=primary),
        model=ModelConfig(provider="openai", model_id="gpt-4"),
        hiring_date=date(2025, 1, 1),
        status=AgentStatus.ACTIVE,
    )


# ---------------------------------------------------------------------------
# Memory ranking benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_rank_memories_500(
    benchmark: pytest.BenchmarkFixture,
) -> None:
    """Benchmark rank_memories with 500 personal + 100 shared."""
    config = _make_retrieval_config()
    personal = tuple(
        _make_memory_entry(
            i,
            hours_ago=i * 0.5,
            relevance=0.5 + (i % 50) / 100,
        )
        for i in range(500)
    )
    shared = tuple(
        _make_memory_entry(
            500 + i,
            hours_ago=i * 2.0,
            relevance=0.6,
        )
        for i in range(100)
    )

    @benchmark
    def _() -> None:
        rank_memories(
            personal,
            config=config,
            now=_NOW,
            shared_entries=shared,
        )


@pytest.mark.benchmark
def test_fuse_ranked_lists_rrf(
    benchmark: pytest.BenchmarkFixture,
) -> None:
    """Benchmark RRF fusion with 3 ranked lists of 200 entries."""
    list_a = tuple(_make_memory_entry(i, relevance=0.9 - i * 0.004) for i in range(200))
    list_b = tuple(
        _make_memory_entry(
            200 + i,
            relevance=0.85 - i * 0.004,
        )
        for i in range(200)
    )
    # Overlapping entries with list_a for dedup testing
    list_c = tuple(
        _make_memory_entry(
            i if i < 50 else 400 + i,
            relevance=0.8 - i * 0.003,
        )
        for i in range(200)
    )
    ranked_lists = (list_a, list_b, list_c)

    @benchmark
    def _() -> None:
        fuse_ranked_lists(ranked_lists, k=60, max_results=50)


@pytest.mark.benchmark
def test_diversity_reranking_mmr(
    benchmark: pytest.BenchmarkFixture,
) -> None:
    """Benchmark MMR diversity re-ranking on 100 scored entries."""
    config = _make_retrieval_config()
    entries = tuple(
        _make_memory_entry(
            i,
            hours_ago=i * 0.2,
            relevance=0.5 + (i % 40) / 100,
            content=(
                f"Topic {i % 10}: detailed analysis of "
                f"subject area {i} with varied vocabulary "
                f"for diversity testing"
            ),
        )
        for i in range(100)
    )
    scored = rank_memories(entries, config=config, now=_NOW)

    @benchmark
    def _() -> None:
        apply_diversity_penalty(scored, diversity_lambda=0.7)


# ---------------------------------------------------------------------------
# DAG analysis benchmarks
# ---------------------------------------------------------------------------


def _build_chain_dag(
    n: int,
) -> tuple[SubtaskDefinition, ...]:
    """Build a linear chain of n subtasks."""
    subtasks = [_make_subtask(0)]
    subtasks.extend(
        _make_subtask(i, dependencies=(f"subtask-{i - 1:03d}",)) for i in range(1, n)
    )
    return tuple(subtasks)


def _build_wide_dag(
    n: int,
    deps_per_node: int = 3,
) -> tuple[SubtaskDefinition, ...]:
    """Build a DAG with fan-in dependencies."""
    subtasks = [_make_subtask(i) for i in range(deps_per_node)]
    for i in range(deps_per_node, n):
        deps = tuple(f"subtask-{i - j - 1:03d}" for j in range(min(deps_per_node, i)))
        subtasks.append(_make_subtask(i, dependencies=deps))
    return tuple(subtasks)


@pytest.mark.benchmark
def test_dag_topological_sort_chain_200(
    benchmark: pytest.BenchmarkFixture,
) -> None:
    """Benchmark topological sort on a 200-node linear chain."""
    subtasks = _build_chain_dag(200)
    graph = DependencyGraph(subtasks)

    @benchmark
    def _() -> None:
        graph.topological_sort()


@pytest.mark.benchmark
def test_dag_parallel_groups_wide_200(
    benchmark: pytest.BenchmarkFixture,
) -> None:
    """Benchmark parallel group computation on a 200-node DAG."""
    subtasks = _build_wide_dag(200, deps_per_node=3)
    graph = DependencyGraph(subtasks)

    @benchmark
    def _() -> None:
        graph.parallel_groups()


# ---------------------------------------------------------------------------
# Agent-task routing benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_agent_task_scoring_50_agents(
    benchmark: pytest.BenchmarkFixture,
) -> None:
    """Benchmark scoring 50 agents against a single subtask."""
    scorer = AgentTaskScorer(min_score=0.1)
    agents = [
        _make_agent(
            i,
            level=list(SeniorityLevel)[i % len(SeniorityLevel)],
            skills=(
                "python",
                "testing",
                "devops",
                "frontend",
            )[: (i % 4) + 1],
        )
        for i in range(50)
    ]
    subtask = SubtaskDefinition(
        id="target-task",
        title="Target task",
        description="A complex task requiring multiple skills",
        estimated_complexity=Complexity.COMPLEX,
        required_skills=("python", "testing", "devops"),
        required_role="engineer",
    )

    @benchmark
    def _() -> None:
        for agent in agents:
            scorer.score(agent, subtask)


# ---------------------------------------------------------------------------
# Coordination metrics benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_coordination_metrics_batch(
    benchmark: pytest.BenchmarkFixture,
) -> None:
    """Benchmark computing all nine coordination metrics."""

    @benchmark
    def _() -> None:
        compute_efficiency(
            success_rate=0.85,
            turns_mas=12.0,
            turns_sas=8.0,
        )
        compute_overhead(turns_mas=12.0, turns_sas=8.0)
        compute_amdahl_ceiling(parallelizable_fraction=0.8)
        compute_straggler_gap(
            agent_durations=[(f"agent-{i}", 10.0 + i * 2.5) for i in range(20)],
        )
        compute_token_speedup_ratio(
            tokens_mas=50000.0,
            tokens_sas=20000.0,
            duration_mas=30.0,
            duration_sas=60.0,
        )
        compute_message_overhead(
            team_size=8,
            message_count=45,
            quadratic_threshold=0.5,
        )
