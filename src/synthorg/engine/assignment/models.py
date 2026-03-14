"""Task assignment domain models.

Frozen Pydantic models for assignment requests, results,
agent workloads, and assignment candidates.
"""

from collections import Counter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.agent import AgentIdentity  # noqa: TC001
from synthorg.core.task import Task  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class AgentWorkload(BaseModel):
    """Snapshot of an agent's current workload.

    Attributes:
        agent_id: Unique agent identifier.
        active_task_count: Number of tasks currently in progress.
        total_cost_usd: Total cost incurred by this agent in USD.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    active_task_count: int = Field(
        ge=0,
        description="Number of tasks currently in progress",
    )
    total_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Total cost incurred by this agent in USD",
    )


class AssignmentCandidate(BaseModel):
    """A candidate agent for task assignment with scoring details.

    Attributes:
        agent_identity: The candidate agent.
        score: Match score between 0.0 and 1.0.
        matched_skills: Skills that matched the assignment requirements.
        reason: Human-readable explanation of the score.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_identity: AgentIdentity = Field(description="Candidate agent")
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Match score (0.0-1.0)",
    )
    matched_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skills that matched assignment requirements",
    )
    reason: NotBlankStr = Field(description="Explanation of score")


class AssignmentRequest(BaseModel):
    """Request for task assignment to an agent.

    The ``required_skills`` and ``required_role`` fields live here
    (not on Task) so that scoring strategies can evaluate agent-task
    fit without modifying the Task model.

    Attributes:
        task: The task to assign.
        available_agents: Pool of agents to consider (must be non-empty,
            unique by agent id).
        workloads: Current workload snapshots per agent (unique by
            agent_id).
        min_score: Minimum score threshold for eligibility.
        required_skills: Skill names needed for scoring.
        required_role: Optional role name for scoring.
        max_concurrent_tasks: Maximum concurrent tasks per agent.
            Agents at or above this limit are excluded from scoring.
            ``None`` disables the limit. Corresponds to
            ``TaskAssignmentConfig.max_concurrent_tasks_per_agent``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task: Task = Field(description="The task to assign")
    available_agents: tuple[AgentIdentity, ...] = Field(
        description="Pool of agents to consider",
    )
    workloads: tuple[AgentWorkload, ...] = Field(
        default=(),
        description="Current workload snapshots per agent",
    )
    min_score: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Minimum score threshold for eligibility",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skill names needed for scoring",
    )
    required_role: NotBlankStr | None = Field(
        default=None,
        description="Optional role name for scoring",
    )
    max_concurrent_tasks: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Maximum concurrent tasks per agent. Agents at or above "
            "this limit are excluded from scoring. None = no limit."
        ),
    )

    @model_validator(mode="after")
    def _validate_collections(self) -> Self:
        """Validate that collections are non-empty and unique."""
        if not self.available_agents:
            msg = "available_agents must not be empty"
            raise ValueError(msg)

        agent_ids = [a.id for a in self.available_agents]
        if len(agent_ids) != len(set(agent_ids)):
            dupes = sorted(str(i) for i, c in Counter(agent_ids).items() if c > 1)
            msg = f"Duplicate agent IDs in available_agents: {dupes}"
            raise ValueError(msg)

        if self.workloads:
            wl_ids = [w.agent_id for w in self.workloads]
            if len(wl_ids) != len(set(wl_ids)):
                dupes = sorted(i for i, c in Counter(wl_ids).items() if c > 1)
                msg = f"Duplicate agent_id in workloads: {dupes}"
                raise ValueError(msg)

        return self


class AssignmentResult(BaseModel):
    """Result of a task assignment operation.

    Attributes:
        task_id: ID of the task that was assigned.
        strategy_used: Name of the strategy that produced this result.
        selected: The selected candidate (None if no viable agent).
        alternatives: Other candidates considered, ranked by score.
        reason: Human-readable explanation of the assignment decision.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task_id: NotBlankStr = Field(description="Task identifier")
    strategy_used: NotBlankStr = Field(
        description="Name of the strategy used",
    )
    selected: AssignmentCandidate | None = Field(
        default=None,
        description="Selected candidate (None if no viable agent)",
    )
    alternatives: tuple[AssignmentCandidate, ...] = Field(
        default=(),
        description="Other candidates considered, ranked by score",
    )
    reason: NotBlankStr = Field(description="Explanation of decision")

    @model_validator(mode="after")
    def _validate_selected_not_in_alternatives(self) -> Self:
        """Ensure selected candidate is not duplicated in alternatives."""
        if self.selected is None:
            return self
        selected_id = self.selected.agent_identity.id
        for alt in self.alternatives:
            if alt.agent_identity.id == selected_id:
                selected_name = self.selected.agent_identity.name
                msg = (
                    f"Selected candidate {selected_name!r} also appears in alternatives"
                )
                raise ValueError(msg)
        return self
