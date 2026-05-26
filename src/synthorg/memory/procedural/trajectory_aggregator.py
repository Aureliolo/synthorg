"""Trajectory aggregation for cross-agent pattern identification.

Collects execution trajectories and identifies patterns that appear
across multiple distinct agents, enabling org-scope skill proposals.
"""

import json
from collections import defaultdict
from typing import Final, Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.skill_evolver import (
    TRAJECTORY_AGGREGATE_COMPLETE,
    TRAJECTORY_AGGREGATE_EMPTY,
)

logger = get_logger(__name__)
_DEFAULT_MIN_AGENTS_FOR_PATTERN: Final[int] = 3


class AggregatedTrajectory(BaseModel):
    """A single execution trajectory for analysis.

    Attributes:
        agent_id: Agent that executed this trajectory.
        task_id: Task identifier.
        outcome: Whether the execution succeeded or failed.
        error_category: Error category (for failures).
        tool_calls: Tool names invoked during execution.
        turn_count: Number of LLM turns.
        recorded_at: When this trajectory was recorded.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Executing agent")
    task_id: NotBlankStr = Field(description="Task identifier")
    outcome: Literal["success", "failure"] = Field(
        description="Execution outcome",
    )
    error_category: NotBlankStr | None = Field(
        default=None,
        description="Error category for failures",
    )
    tool_calls: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tool names invoked",
    )
    turn_count: int = Field(ge=0, description="LLM turns completed")
    recorded_at: AwareDatetime = Field(
        description="When trajectory was recorded",
    )

    @model_validator(mode="after")
    def _validate_error_consistency(self) -> Self:
        """Ensure error_category presence matches outcome.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.outcome == "failure" and self.error_category is None:
            msg = "error_category required when outcome is 'failure'"
            raise ValueError(msg)
        if self.outcome == "success" and self.error_category is not None:
            msg = "error_category must be None when outcome is 'success'"
            raise ValueError(msg)
        return self


class TrajectoryPattern(BaseModel):
    """A pattern identified across multiple trajectories.

    Attributes:
        pattern_id: Unique pattern identifier.
        description: Human-readable pattern description.
        agent_ids: Distinct agents that hit this pattern.
        occurrence_count: Total trajectory count.
        failure_rate: Fraction of trajectories that failed.
        representative_trajectory: Example trajectory for context.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    pattern_id: NotBlankStr = Field(description="Unique identifier")
    description: NotBlankStr = Field(description="Pattern description")
    agent_ids: frozenset[NotBlankStr] = Field(
        description="Distinct agents",
    )
    occurrence_count: int = Field(ge=1, description="Total occurrences")
    failure_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction that failed",
    )
    representative_trajectory: AggregatedTrajectory = Field(
        description="Example trajectory",
    )


def _group_key(trajectory: AggregatedTrajectory) -> str:
    """Compute a grouping key for a trajectory.

    Failures group by error_category; successes group by
    tool call sequence.

    Returns:
        Result of type ``str``.
    """
    if trajectory.outcome == "failure" and trajectory.error_category:
        return f"failure:{trajectory.error_category}"
    return f"success:{json.dumps(list(trajectory.tool_calls))}"


class TrajectoryAggregator:
    """Identifies cross-agent patterns from execution trajectories.

    Stateful: ``last_skipped_count`` is updated on each
    ``aggregate()`` call so the evolver can report how many groups
    fell below the agent threshold.

    Args:
        min_agents_for_pattern: Minimum distinct agents required
            to form a pattern (default 3).
    """

    __slots__ = ("_min_agents", "last_skipped_count")

    def __init__(
        self, *, min_agents_for_pattern: int = _DEFAULT_MIN_AGENTS_FOR_PATTERN
    ) -> None:
        if min_agents_for_pattern <= 0:
            msg = (
                f"min_agents_for_pattern must be positive, got {min_agents_for_pattern}"
            )
            raise ValueError(msg)
        self._min_agents = min_agents_for_pattern
        self.last_skipped_count: int = 0

    def aggregate(
        self,
        trajectories: tuple[AggregatedTrajectory, ...],
    ) -> tuple[TrajectoryPattern, ...]:
        """Group trajectories by pattern and filter by threshold.

        Args:
            trajectories: All trajectories to analyze.

        Returns:
            Patterns seen by >= ``min_agents_for_pattern`` distinct
            agents, sorted by occurrence count descending.
        """
        if not trajectories:
            self.last_skipped_count = 0
            logger.debug(TRAJECTORY_AGGREGATE_EMPTY)
            return ()

        groups: dict[str, list[AggregatedTrajectory]] = defaultdict(list)
        for t in trajectories:
            groups[_group_key(t)].append(t)

        patterns: list[TrajectoryPattern] = []
        skipped = 0
        for key, trajs in groups.items():
            agent_ids = frozenset(t.agent_id for t in trajs)
            if len(agent_ids) < self._min_agents:
                skipped += 1
                continue

            failure_count = sum(1 for t in trajs if t.outcome == "failure")
            failure_rate = failure_count / len(trajs)

            patterns.append(
                TrajectoryPattern(
                    pattern_id=str(uuid4()),
                    description=f"Pattern: {key} "
                    f"({len(trajs)} occurrences, "
                    f"{len(agent_ids)} agents)",
                    agent_ids=agent_ids,
                    occurrence_count=len(trajs),
                    failure_rate=failure_rate,
                    representative_trajectory=trajs[0],
                ),
            )

        self.last_skipped_count = skipped
        patterns.sort(key=lambda p: p.occurrence_count, reverse=True)
        logger.debug(
            TRAJECTORY_AGGREGATE_COMPLETE,
            groups=len(groups),
            patterns=len(patterns),
            skipped=skipped,
        )
        return tuple(patterns)
