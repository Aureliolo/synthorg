# module-kind: service
"""Read access to the evaluate stage's judgements on a plan.

Separate from :class:`~synthorg.api.services.plan_service.PlanService`, which
owns the plan row's audited lifecycle writes. This reads a different store
(``initiative_evaluation_report``), takes no lifecycle decision, and shares no
state with those writes: keeping it here is what stops the plan service from
carrying two unrelated responsibilities behind one constructor.
"""

from typing import Final

from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportFilterSpec,
    EvaluationReportRecord,
    EvaluationReportRepository,
)

#: Attempts returned for one plan. Matches the evaluate stage's own retry
#: ceiling, so the history is complete rather than truncated.
MAX_EVALUATION_ATTEMPTS: Final[int] = 20


class PlanEvaluationService:
    """Serve the recorded verdicts for a plan, newest first."""

    def __init__(self, *, reports: EvaluationReportRepository | None) -> None:
        """Bind the judgement store, which a deployment may not have wired.

        Args:
            reports: The judgement store, or ``None`` when none is wired.
        """
        self._reports = reports

    async def history(self, plan_id: NotBlankStr) -> tuple[EvaluationReportRecord, ...]:
        """Return the evaluate stage's judgements for *plan_id*, newest first.

        Returns:
            The recorded judgements, bounded by
            :data:`MAX_EVALUATION_ATTEMPTS`.

        Raises:
            ServiceUnavailableError: When no judgement store is wired, so an
                empty history cannot be told apart from a plan that has never
                been judged.
        """
        if self._reports is None:
            msg = "Plan evaluation history is unavailable: no judgement store wired"
            raise ServiceUnavailableError(msg)
        return await self._reports.query(
            EvaluationReportFilterSpec(plan_id=plan_id),
            limit=MAX_EVALUATION_ATTEMPTS,
        )
