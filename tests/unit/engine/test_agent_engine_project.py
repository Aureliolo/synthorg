"""Tests for AgentEngine project validation and budget integration.

Dispatch checks that the project EXISTS and that its budget is not spent.
It no longer asks whether the agent was staffed on it: nothing in the loop
ever wrote a project's roster subset, and confinement is structural anyway
(the per-project workspace root, the sandbox container key, forge
repo-scoping and the SecOps action-type gate).
"""

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
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.errors import ProjectNotFoundError
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
    budget: float = 0.0,
) -> Project:
    return Project(
        id=as_uuid(project_id),
        name="Test Project",
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
        sample_agent: AgentIdentity,
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
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

    async def test_any_agent_may_work_an_existing_project(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Selection decided who takes the work; dispatch does not re-decide."""
        repo = _make_project_repo(project=_make_project())
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            project_repo=repo,
        )

        result = await engine.run(
            identity=sample_agent,
            task=sample_task_with_criteria,
        )
        assert result.termination_reason == TerminationReason.COMPLETED

    @pytest.mark.parametrize(
        "role",
        [COMPLETION_REVIEWER_ROLE_NAME, RED_TEAM_ROLE_NAME],
    )
    async def test_a_gate_role_dispatches_like_any_other(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
        role: str,
    ) -> None:
        """With no membership check there is nothing for a gate role to be
        exempt from, so the two paths are the same path."""
        judge = sample_agent.model_copy(update={"role": role})
        repo = _make_project_repo(project=_make_project())
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider, project_repo=repo)

        result = await engine.run(identity=judge, task=sample_task_with_criteria)

        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_a_gate_role_still_needs_the_project_to_exist(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A missing project is a broken dispatch for a reviewer exactly as
        for a working agent."""
        reviewer = sample_agent.model_copy(update={"role": RED_TEAM_ROLE_NAME})
        repo = _make_project_repo(project=None)
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider, project_repo=repo)

        with pytest.raises(ProjectNotFoundError):
            await engine.run(identity=reviewer, task=sample_task_with_criteria)

    async def test_no_project_repo_skips_validation(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Without project_repo, project checks are skipped with warning."""
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent,
            task=sample_task_with_criteria,
        )
        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestProjectBudgetIntegration:
    """Tests for project budget enforcement in AgentEngine.run()."""

    async def test_project_budget_exceeded_returns_budget_exhausted(
        self,
        sample_agent: AgentIdentity,
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
            identity=sample_agent,
            task=sample_task_with_criteria,
        )
        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
