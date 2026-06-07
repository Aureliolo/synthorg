"""Tests for engine input validation functions."""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import AgentStatus, TaskStatus
from synthorg.core.task import Task
from synthorg.engine._validation import (
    validate_agent,
    validate_run_inputs,
    validate_task,
)
from synthorg.engine.errors import ExecutionStateError


@pytest.mark.unit
class TestValidateRunInputs:
    """validate_run_inputs rejects invalid scalar arguments."""

    def test_rejects_zero_max_turns(self) -> None:
        with pytest.raises(ValueError, match="max_turns must be >= 1"):
            validate_run_inputs(
                agent_id="a1",
                task_id="t1",
                max_turns=0,
                timeout_seconds=None,
            )

    def test_rejects_negative_max_turns(self) -> None:
        with pytest.raises(ValueError, match="max_turns must be >= 1"):
            validate_run_inputs(
                agent_id="a1",
                task_id="t1",
                max_turns=-5,
                timeout_seconds=None,
            )

    def test_rejects_zero_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            validate_run_inputs(
                agent_id="a1",
                task_id="t1",
                max_turns=10,
                timeout_seconds=0,
            )

    def test_rejects_negative_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            validate_run_inputs(
                agent_id="a1",
                task_id="t1",
                max_turns=10,
                timeout_seconds=-1.0,
            )

    def test_accepts_valid_inputs(self) -> None:
        validate_run_inputs(
            agent_id="a1",
            task_id="t1",
            max_turns=20,
            timeout_seconds=30.0,
        )

    def test_accepts_none_timeout(self) -> None:
        validate_run_inputs(
            agent_id="a1",
            task_id="t1",
            max_turns=1,
            timeout_seconds=None,
        )


@pytest.mark.unit
class TestValidateAgent:
    """validate_agent rejects non-ACTIVE agents."""

    def test_rejects_onboarding_agent(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        agent = sample_agent_with_personality.model_copy(
            update={"status": AgentStatus.ONBOARDING},
        )
        with pytest.raises(ExecutionStateError, match="onboarding"):
            validate_agent(agent, str(agent.id))

    def test_rejects_terminated_agent(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        agent = sample_agent_with_personality.model_copy(
            update={"status": AgentStatus.TERMINATED},
        )
        with pytest.raises(ExecutionStateError, match="terminated"):
            validate_agent(agent, str(agent.id))

    def test_accepts_active_agent(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        validate_agent(
            sample_agent_with_personality,
            str(sample_agent_with_personality.id),
        )


@pytest.mark.unit
class TestValidateTask:
    """validate_task rejects non-executable or wrongly assigned tasks."""

    def test_rejects_created_task(
        self,
        sample_task_with_criteria: Task,
    ) -> None:
        task = sample_task_with_criteria.model_copy(
            update={"status": TaskStatus.CREATED},
        )
        with pytest.raises(ExecutionStateError, match="created"):
            validate_task(task, agent_id="a1", task_id=str(task.id))

    def test_rejects_completed_task(
        self,
        sample_task_with_criteria: Task,
    ) -> None:
        task = sample_task_with_criteria.model_copy(
            update={"status": TaskStatus.COMPLETED},
        )
        with pytest.raises(ExecutionStateError, match="completed"):
            validate_task(task, agent_id="a1", task_id=str(task.id))

    def test_rejects_wrong_assignee(
        self,
        sample_task_with_criteria: Task,
    ) -> None:
        with pytest.raises(ExecutionStateError, match="not to agent"):
            validate_task(
                sample_task_with_criteria,
                agent_id="wrong-agent",
                task_id=str(sample_task_with_criteria.id),
            )

    def test_accepts_assigned_task(
        self,
        sample_task_with_criteria: Task,
    ) -> None:
        assert sample_task_with_criteria.assigned_to is not None
        validate_task(
            sample_task_with_criteria,
            agent_id=sample_task_with_criteria.assigned_to,
            task_id=str(sample_task_with_criteria.id),
        )

    def test_accepts_in_progress_task(
        self,
        sample_task_with_criteria: Task,
    ) -> None:
        task = sample_task_with_criteria.model_copy(
            update={"status": TaskStatus.IN_PROGRESS},
        )
        assert task.assigned_to is not None
        validate_task(
            task,
            agent_id=task.assigned_to,
            task_id=str(task.id),
        )
