"""Quality scoring strategy protocol.

Defines the interface for pluggable quality scoring strategies
that evaluate task completion quality (see Agents design page, D2).
"""

from typing import Protocol, runtime_checkable

from synthorg.core.task import AcceptanceCriterion  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.performance.models import (
    QualityScoreResult,  # noqa: TC001
    TaskMetricRecord,  # noqa: TC001
)


@runtime_checkable
class QualityScoringStrategy(Protocol):
    """Strategy for scoring task completion quality.

    Implementations evaluate task results against acceptance criteria
    and other quality signals to produce a normalized score.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    async def score(
        self,
        *,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        task_result: TaskMetricRecord,
        acceptance_criteria: tuple[AcceptanceCriterion, ...],
    ) -> QualityScoreResult:
        """Score task completion quality.

        Args:
            agent_id: Agent who completed the task.
            task_id: Task identifier.
            task_result: Recorded task metrics.
            acceptance_criteria: Criteria to evaluate against.

        Returns:
            Quality score result with breakdown and confidence.
        """
        ...
