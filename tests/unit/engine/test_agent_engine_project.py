"""Tests for AgentEngine project validation and budget integration."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.core.enums import ProjectStatus
from synthorg.core.project import Project
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.errors import (
    ProjectAgentNotMemberError,
    ProjectNotFoundError,
)
from synthorg.engine.loop_protocol import TerminationReason
from tests._shared import as_uuid

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

from .conftest import (
    MockCompletionProvider,
    make_completion_response,
)


def _make_project(
    *,
    project_id: str = "proj-001",
    team: tuple[str, ...] = (),
    budget: float = 0.0,
) -> Project:
    return Project(
        id=as_uuid(project_id),
        name="Test Project",
        team=team,
        budget=budget,
        status=ProjectStatus.ACTIVE,
    )


def _make_project_repo(
    project: Project | None = None,
) -> AsyncMock:
    """Create a mock project repository."""
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=project)
    return repo


@pytest.mark.unit
class TestProjectValidation:
    """Tests for project validation in AgentEngine.run()."""

    async def test_project_not_found_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Raises ProjectNotFoundError when project repo returns None."""
        repo = _make_project_repo(project=None)
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            project_repo=repo,
        )

        with pytest.raises(ProjectNotFoundError):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

    async def test_agent_not_in_team_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Raises ProjectAgentNotMemberError when agent not in team."""
        project = _make_project(
            project_id="proj-001",
            team=("other-agent-1", "other-agent-2"),
        )
        repo = _make_project_repo(project=project)
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            project_repo=repo,
        )

        with pytest.raises(ProjectAgentNotMemberError):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

    async def test_empty_team_allows_any_agent(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Empty team means no membership restriction."""
        project = _make_project(project_id="proj-001", team=())
        repo = _make_project_repo(project=project)
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            project_repo=repo,
        )

        # Should not raise -- proceeds to execution
        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_agent_in_team_proceeds(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Agent in project team passes validation."""
        agent_id = str(sample_agent_with_personality.id)
        project = _make_project(
            project_id="proj-001",
            team=(agent_id,),
        )
        repo = _make_project_repo(project=project)
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            project_repo=repo,
        )

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_no_project_repo_skips_validation(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Without project_repo, project checks are skipped with warning."""
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestProjectBudgetIntegration:
    """Tests for project budget enforcement in AgentEngine.run()."""

    async def test_project_budget_exceeded_returns_budget_exhausted(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Project budget exceeded returns BUDGET_EXHAUSTED."""
        cfg = BudgetConfig(
            total_monthly=1000.0,
            alerts=BudgetAlertConfig(
                warn_at=75,
                critical_at=90,
                hard_stop_at=100,
            ),
        )
        tracker = CostTracker(budget_config=cfg)
        from tests.unit.budget.conftest import make_cost_record

        await tracker.record(make_cost_record(project_id="proj-001", cost=50.0))
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        # Project has budget=10.0 but we already spent 50.0
        project = _make_project(
            project_id="proj-001",
            budget=10.0,
        )
        repo = _make_project_repo(project=project)

        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            budget_enforcer=enforcer,
            project_repo=repo,
        )

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
