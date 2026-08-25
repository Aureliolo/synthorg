# module-kind: code
"""Wire-to-domain translation for the plan routes.

The controller owns these mappings so the service layer stays free of any
``api.dto_*`` dependency, which the persistence/service layering gate
enforces. They are pure functions of their argument with no controller state,
which is why they sit beside the routes rather than inside them.
"""

from synthorg.api.dto_plans import PlanItemPayload
from synthorg.core.domain_errors import ValidationError
from synthorg.core.plan import PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_VALIDATION_FAILED

logger = get_logger(__name__)


def item_from_payload(payload: PlanItemPayload) -> PlanItem:
    """Project an edit-request item onto a durable plan item.

    Returns:
        A :class:`PlanItem` carrying the payload's fields verbatim.
    """
    return PlanItem(
        id=payload.id,
        title=payload.title,
        description=payload.description,
        parent_id=payload.parent_id,
        dependencies=payload.dependencies,
        owner=payload.owner,
        acceptance_criteria=payload.acceptance_criteria,
        expected_artifacts=payload.expected_artifacts,
        required_skills=payload.required_skills,
        required_tags=payload.required_tags,
        estimated_complexity=payload.estimated_complexity,
        stakes=payload.stakes,
        kind=payload.kind,
        options=payload.options,
        chosen_option_id=payload.chosen_option_id,
        satisfies=payload.satisfies,
    )


def parse_status(status: NotBlankStr | None) -> PlanStatus | None:
    """Parse an optional plan-status query filter.

    Returns:
        The parsed :class:`PlanStatus`, or ``None`` when unset.

    Raises:
        ValidationError: ``status`` is not a valid :class:`PlanStatus`.
    """
    if status is None:
        return None
    try:
        return PlanStatus(status)
    except ValueError as exc:
        valid = ", ".join(e.value for e in PlanStatus)
        msg = f"Invalid plan status: {status!r}. Valid values: {valid}"
        logger.warning(
            API_VALIDATION_FAILED,
            reason="invalid_plan_status",
            status=status,
            valid=valid,
        )
        raise ValidationError(msg) from exc


__all__ = ["item_from_payload", "parse_status"]
