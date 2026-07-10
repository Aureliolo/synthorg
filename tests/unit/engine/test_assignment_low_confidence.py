"""Unit tests for the assignment low-confidence band (stakes-aware)."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.role import Authority, Skill
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Stakes, TaskType
from synthorg.engine.assignment.models import AssignmentRequest
from synthorg.engine.assignment.pool_filters import IdentityPoolFilter
from synthorg.engine.assignment.rankers import ScoreDescendingRanker
from synthorg.engine.assignment.scoring_based import ScoringBasedAssignmentStrategy
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.hr.enums import AgentStatus
from synthorg.hr.seniority import SeniorityLevel
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def _agent(name: str, *, primary: tuple[str, ...] = ()) -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(name),
        name=name,
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        skills=SkillSet(
            primary=tuple(Skill(id=s, name=s) for s in primary),
        ),
        authority=Authority(budget_limit=100.0),
        model=ModelConfig(provider="p", model_id="m", model_tier="small"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


def _task(stakes: Stakes) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="A task",
        description="Body",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="creator",
        stakes=stakes,
        estimated_complexity=Complexity.SIMPLE,
    )


def _strategy() -> ScoringBasedAssignmentStrategy:
    return ScoringBasedAssignmentStrategy(
        name="role_based",
        scorer=AgentTaskScorer(),
        pool_filter=IdentityPoolFilter(),
        ranker=ScoreDescendingRanker(),
    )


def _request(
    stakes: Stakes,
    *,
    required_skills: tuple[str, ...] = (),
) -> AssignmentRequest:
    # A MID agent on a SIMPLE task with no matching skills scores 0.2 (the
    # seniority-alignment bonus only): above min_score 0.1 but below the 0.35
    # low-confidence band.
    return AssignmentRequest(
        task=_task(stakes),
        available_agents=(_agent("weak"),),
        required_skills=required_skills,
        stakes=stakes,
    )


async def test_low_stakes_marginal_fit_proceeds_flagged() -> None:
    result = _strategy().assign(_request(Stakes.NORMAL))
    assert result.selected is not None
    assert result.selected.score == pytest.approx(0.2)
    assert result.low_confidence is True


@pytest.mark.parametrize("stakes", [Stakes.HIGH, Stakes.CRITICAL])
async def test_high_critical_marginal_fit_proceeds_flagged(stakes: Stakes) -> None:
    # A marginal fit for high/critical work is never a hard-fail: assign the
    # best available agent (never deadlock the org) and flag it low_confidence
    # so the WARNING + dashboard flag surface the risk for operator review.
    result = _strategy().assign(_request(stakes))
    assert result.selected is not None
    assert result.low_confidence is True


async def test_confident_fit_is_not_flagged_at_any_stakes() -> None:
    # A primary-skill match lifts the score to 0.4 + 0.2 seniority = 0.6, above
    # the band, so high-stakes work proceeds with a confident fit.
    request = AssignmentRequest(
        task=_task(Stakes.HIGH),
        available_agents=(_agent("strong", primary=("python",)),),
        required_skills=("python",),
        stakes=Stakes.HIGH,
    )
    result = _strategy().assign(request)
    assert result.selected is not None
    assert result.low_confidence is False


def test_low_confidence_band_clamps_to_min_score() -> None:
    # A raised eligibility floor above the band collapses the marginal zone:
    # the effective band never sits below min_score.
    request = AssignmentRequest(
        task=_task(Stakes.NORMAL),
        available_agents=(_agent("a"),),
        min_score=0.5,
        low_confidence_score=0.3,
    )
    assert request.effective_low_confidence_score == 0.5
