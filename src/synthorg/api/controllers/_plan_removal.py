# module-kind: code
"""Remove a plan, with everything a deletion owes done in the right order.

Read the plan first: after the row is gone nothing can say what the surviving
approval, decision and cost rows are naming. Bind the requester before anything
is written, because an unbound one is a server fault and a fault must not first
expire the plan's review approval and then refuse the delete. Scope the
approval retirement to the delete itself, or a plan refused for still building
loses the reviews that were answerable. Then the tombstone, then the event the
review inbox listens on.

The bulk form is here beside the single one because it IS the single one
repeated: what differs is that a refusal is collected against its row instead
of ending the request, and a plan refuses on its own terms often enough
(building items, a review just decided) that a mixed selection is the normal
case rather than the exception.
"""

from litestar import Request
from litestar.datastructures import State

from synthorg.api.channels import (
    CHANNEL_PLANS,
    plan_updated_payload,
    publish_ws_event,
)
from synthorg.api.controllers._approval_retire import retiring_plan_approvals
from synthorg.api.controllers._bulk_delete import (
    BulkDeleteResult,
    resolve_bulk_delete_budget,
    run_bulk_delete,
)
from synthorg.api.controllers._deletion_record import record_deletion
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.plan_service import PlanService
from synthorg.api.ws_models import WsEventType
from synthorg.core.deleted_entity import DeletedEntityKind
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.persistence.state import persistence_of


async def remove_plan(
    request: Request[object, object, State],
    state: State,
    service: PlanService,
    plan_id: str,
    *,
    requested_by: str,
) -> None:
    """Delete *plan_id*, retiring the approvals parked against it.

    Args:
        request: The incoming request, for the WebSocket publish.
        state: Application state.
        service: The plan service the route already built.
        plan_id: The plan to remove.
        requested_by: The person who asked.

    Raises:
        NotFoundError: No plan with ``plan_id`` exists.
        PlanNotDeletableError: The plan's items are still building, or it is
            already decided.
        ConflictError: The parked review approval was decided while the delete
            was being prepared, so the plan is still being acted on.
    """
    existing = require_resource_or_404(
        await service.get(plan_id),
        resource_type="Plan",
        identifier=plan_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation="delete",
    )
    async with retiring_plan_approvals(state.app_state, str(existing.id)) as retirement:
        # Live work is counted inside the delete, in the same statement, so a
        # task filed between the two cannot be stranded on a deleted plan.
        await service.delete(existing, requested_by=requested_by)
        retirement.removed(str(existing.id))
    # Whichever route removed it, the records that outlive a plan keep naming
    # its id, so the tombstone is what they resolve against.
    await record_deletion(
        persistence_of(state.app_state),
        kind=DeletedEntityKind.PLAN,
        entity_id=str(existing.id),
        display_name=existing.objective_title,
        deleted_by=requested_by,
    )
    # The review inbox and any open detail view drop it on the same event
    # every other plan mutation publishes.
    publish_ws_event(
        request,
        WsEventType.PLAN_UPDATED,
        CHANNEL_PLANS,
        plan_updated_payload(existing),
    )


async def remove_plans(
    request: Request[object, object, State],
    state: State,
    service: PlanService,
    ids: tuple[NotBlankStr, ...],
    *,
    requested_by: str,
) -> BulkDeleteResult:
    """Delete every plan in *ids*, collecting the ones that refuse.

    Args:
        request: The incoming request, for the WebSocket publishes.
        state: Application state.
        service: The plan service the route already built.
        ids: The plans the operator selected.
        requested_by: The person who asked.

    Returns:
        What was removed and what remains.
    """
    return await run_bulk_delete(
        ids,
        lambda plan_id: remove_plan(
            request, state, service, plan_id, requested_by=requested_by
        ),
        entity="plan",
        clock=state.app_state.clock,
        budget_seconds=await resolve_bulk_delete_budget(state.app_state),
    )


__all__ = ["remove_plan", "remove_plans"]
