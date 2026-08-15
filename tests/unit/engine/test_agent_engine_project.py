"""Tests for AgentEngine project validation and budget integration."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.role_catalog import (
    COMPLETION_REVIEWER_ROLE_NAME,
    RED_TEAM_ROLE_NAME,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.completion_oracle.runtime_context import (
    CompletionOracleRuntimeContext,
    completion_oracle_runtime_context,
)
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

    async def test_gate_role_reaches_a_project_it_is_not_staffed_on(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A Completion Reviewer judges work on any project, staffed or not.

        The exemption is scoped to the gate's own dispatch, so the test runs
        inside the trusted context the gate binds around its reviewer run.
        """
        reviewer = sample_agent_with_personality.model_copy(
            update={"role": COMPLETION_REVIEWER_ROLE_NAME}
        )
        project = _make_project(
            project_id="proj-001",
            team=("other-agent-1", "other-agent-2"),
        )
        repo = _make_project_repo(project=project)
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider, project_repo=repo)

        ctx = CompletionOracleRuntimeContext(
            execution_id=NotBlankStr("exec-1"),
            task_id=NotBlankStr(str(sample_task_with_criteria.id)),
            reviewer_agent_id=NotBlankStr(str(reviewer.id)),
            executor_agent_id=NotBlankStr("executor-1"),
        )
        with completion_oracle_runtime_context(ctx):
            result = await engine.run(identity=reviewer, task=sample_task_with_criteria)
        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_gate_role_is_confined_on_ordinary_work(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Reach belongs to the judging, not to the judge.

        A gate-role holder handed an ordinary task on a project it is not
        staffed on is an ordinary working agent, and the team check is the
        only thing keeping one project's agent out of another's workspace
        and budget. Outside a gate dispatch the exemption does not apply.
        """
        reviewer = sample_agent_with_personality.model_copy(
            update={"role": COMPLETION_REVIEWER_ROLE_NAME}
        )
        project = _make_project(
            project_id="proj-001",
            team=("other-agent-1", "other-agent-2"),
        )
        repo = _make_project_repo(project=project)
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider, project_repo=repo)

        with pytest.raises(ProjectAgentNotMemberError):
            await engine.run(identity=reviewer, task=sample_task_with_criteria)

    async def test_gate_role_still_needs_the_project_to_exist(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Reach exempts membership, never existence: a missing project is a
        broken dispatch for a reviewer exactly as for a working agent."""
        reviewer = sample_agent_with_personality.model_copy(
            update={"role": RED_TEAM_ROLE_NAME}
        )
        repo = _make_project_repo(project=None)
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider, project_repo=repo)

        with pytest.raises(ProjectNotFoundError):
            await engine.run(identity=reviewer, task=sample_task_with_criteria)

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
