"""Promotion criteria strategy protocol.

Defines the pluggable interface for evaluating promotion/demotion criteria.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ai_company.core.enums import SeniorityLevel
    from ai_company.core.types import NotBlankStr
    from ai_company.hr.performance.models import AgentPerformanceSnapshot
    from ai_company.hr.promotion.models import PromotionEvaluation


@runtime_checkable
class PromotionCriteriaStrategy(Protocol):
    """Protocol for promotion criteria evaluation.

    Implementations define what criteria must be met for an agent
    to be promoted or demoted between seniority levels.
    """

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        ...

    async def evaluate(
        self,
        *,
        agent_id: NotBlankStr,
        current_level: SeniorityLevel,
        target_level: SeniorityLevel,
        snapshot: AgentPerformanceSnapshot,
    ) -> PromotionEvaluation:
        """Evaluate whether an agent meets criteria for level change.

        Args:
            agent_id: Agent to evaluate.
            current_level: Current seniority level.
            target_level: Target seniority level.
            snapshot: Agent performance snapshot.

        Returns:
            Evaluation result with criteria details.
        """
        ...
