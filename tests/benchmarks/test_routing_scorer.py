"""CodSpeed benchmarks for the agent-task routing scorer.

The scorer is called per ``(agent, subtask)`` pair during routing,
which is on the hot path of every workflow execution. Single-agent
and batch (10-agent) scenarios cover both the per-call cost and the
amortised loop cost.
"""

from datetime import date

import pytest
from pytest_codspeed import BenchmarkFixture

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import AgentStatus, Complexity, SeniorityLevel
from synthorg.core.role import Skill
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.routing.scorer import AgentTaskScorer


def _make_skill(idx: int) -> Skill:
    return Skill(
        id=f"skill-{idx}",
        name=f"Skill {idx}",
        tags=(f"tag-{idx % 3}",),
        proficiency=0.7 + (idx % 4) * 0.1,
    )


@pytest.mark.benchmark
def test_agent_task_scorer_single(benchmark: BenchmarkFixture) -> None:
    """Score a single agent against a subtask (full scoring path)."""
    skills = tuple(_make_skill(i) for i in range(5))
    agent = AgentIdentity(
        name="Test Agent",
        role="backend developer",
        department="engineering",
        level=SeniorityLevel.SENIOR,
        status=AgentStatus.ACTIVE,
        skills=SkillSet(primary=skills[:3], secondary=skills[3:]),
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
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
def test_agent_task_scorer_batch_10(benchmark: BenchmarkFixture) -> None:
    """Score 10 agents against a subtask (batch routing)."""
    levels = list(SeniorityLevel)
    agents = []
    for a in range(10):
        skill_count = a % 4 + 2
        skills = tuple(
            Skill(
                id=f"skill-{s}",
                name=f"Skill {s}",
                tags=(f"tag-{s % 3}",),
                proficiency=0.6 + (s % 5) * 0.08,
            )
            for s in range(skill_count)
        )
        agents.append(
            AgentIdentity(
                name=f"Test Agent {a}",
                role="backend developer" if a % 3 == 0 else "frontend developer",
                department="engineering",
                level=levels[a % len(levels)],
                status=AgentStatus.ACTIVE,
                skills=SkillSet(
                    primary=skills[: skill_count // 2],
                    secondary=skills[skill_count // 2 :],
                ),
                model=ModelConfig(
                    provider="test-provider",
                    model_id="test-small-001",
                ),
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
