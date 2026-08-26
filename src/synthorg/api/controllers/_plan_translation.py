# module-kind: code
"""Wire-to-domain translation for the plan routes.

The controller owns these mappings so the service layer stays free of any
``api.dto_*`` dependency, which the persistence/service layering gate
enforces. They are pure functions of their argument with no controller state,
which is why they sit beside the routes rather than inside them.
"""

from collections.abc import Sequence

from synthorg.api.dto_plans import PlanItemPayload
from synthorg.core.domain_errors import ValidationError
from synthorg.core.plan import PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_VALIDATION_FAILED

logger = get_logger(__name__)


def _still_the_same_size(payload: PlanItemPayload, previous: PlanItem) -> bool:
    """Whether the operator left everything the size signal reads alone.

    Returns:
        ``True`` when all three fields atomicity is judged on are unchanged.
        Compared by value rather than by count, because a same-length rewrite
        is a different unit doing different work, and the note the previous
        version carried was written about the old wording.
    """
    return (
        payload.expected_artifacts == previous.expected_artifacts
        and payload.acceptance_criteria == previous.acceptance_criteria
        and payload.satisfies == previous.satisfies
    )


def item_from_payload(
    payload: PlanItemPayload, *, previous: PlanItem | None
) -> PlanItem:
    """Project an edit-request item onto a durable plan item.

    ``unsplit_reason`` is not a payload field, because an operator revising an
    item has replaced the version the note was written about. But an edit
    replaces the WHOLE list, so reading its absence as "cleared" wiped the
    oversized notes off every item an operator did not touch, which is the
    reviewer-facing flag the plan carries them for. It therefore survives on
    an item whose size the operator left alone, and is dropped the moment they
    change any count the signal is judged on.

    Args:
        payload: The submitted item.
        previous: The item it replaces, or ``None`` when it is new.

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
        unsplit_reason=(
            previous.unsplit_reason
            if previous is not None and _still_the_same_size(payload, previous)
            else None
        ),
    )


def items_from_payloads(
    payloads: Sequence[PlanItemPayload], *, previous: Sequence[PlanItem]
) -> tuple[PlanItem, ...]:
    """Project a whole submitted item list against the one it replaces.

    The plural is the entry point rather than a convenience over the singular,
    because the singular needs the item it supersedes and a caller holding a
    list is the only thing that can find it.

    Args:
        payloads: The submitted list, in the order the operator sent it.
        previous: The plan's current items.

    Returns:
        The durable items.
    """
    was = {item.id: item for item in previous}
    return tuple(
        item_from_payload(payload, previous=was.get(payload.id)) for payload in payloads
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


__all__ = ["item_from_payload", "items_from_payloads", "parse_status"]
