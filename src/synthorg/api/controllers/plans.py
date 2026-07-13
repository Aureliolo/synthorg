"""Plan controller -- read, rework, and request-changes on durable plans.

The first-class surface for the plan-review workspace. Approve / reject stay
on the ``/approvals`` decision endpoints (the workspace holds the linked
``approval_id``): those drive the single, idempotent decision + dispatch path,
so this controller deliberately does not duplicate them. It owns the
plan-native capabilities the approval flow lacks -- reading the durable plan,
reworking its items, and sending it back for revision.
"""

from typing import Annotated, Final

from litestar import Controller, Request, Response, get, patch, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.channels import CHANNEL_PLANS, publish_ws_event
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_plans import (
    EditPlanRequest,
    PlanItemPayload,
    RequestPlanChangesRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.plan_service import PlanService
from synthorg.api.ws_models import WsEventType
from synthorg.core.domain_errors import ValidationError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.persistence.state import persistence_of

_DEFAULT_LIMIT: Final[int] = 50


def _service(state: State) -> PlanService:
    """Build the per-request :class:`PlanService` instance.

    Returns:
        ``PlanService`` bound to this backend's plan repository and clock.
    """
    return PlanService(
        repo=persistence_of(state.app_state).plans,
        clock=state.app_state.clock,
    )


def _item_from_payload(payload: PlanItemPayload) -> PlanItem:
    """Project an edit-request item onto a durable plan item.

    The controller owns this DTO -> domain mapping so the service layer stays
    free of any ``api.dto_*`` dependency (persistence/service layering gate).

    Returns:
        A :class:`PlanItem` carrying the payload's fields verbatim.
    """
    return PlanItem(
        id=payload.id,
        title=payload.title,
        description=payload.description,
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
    )


PlanStatusFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by plan lifecycle status",
    ),
]

PlanProjectFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by project id",
    ),
]

PlanObjectiveFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by the charter/objective the plan serves",
    ),
]


class PlanController(Controller):
    """Controller for plan reading, reworking, and revision requests."""

    path = "/plans"
    tags = ("plans",)

    @get(guards=[require_read_access])
    async def list_plans(  # noqa: PLR0913 -- pagination + three orthogonal filters
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
        status: PlanStatusFilter = None,
        project: PlanProjectFilter = None,
        objective_id: PlanObjectiveFilter = None,
    ) -> PaginatedResponse[Plan]:
        """List plans with optional filters.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.
            status: Filter by plan lifecycle status.
            project: Filter by project id.
            objective_id: Filter by the charter/objective the plan serves.

        Returns:
            Paginated list of plans.

        Raises:
            ValidationError: ``status`` is not a valid :class:`PlanStatus`.
        """
        parsed_status = _parse_status(status)
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        # Fetch ``limit + 1`` at the decoded offset so the next-page flag comes
        # from a bounded window and the cursor walks the whole result set
        # rather than re-reading the first page.
        plans = await _service(state).list_plans(
            status=parsed_status,
            project=project,
            objective_id=objective_id,
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(plans),
            limit=limit,
            secret=secret,
        )
        return PaginatedResponse[Plan](data=plans[:limit], pagination=meta)

    @get("/{plan_id:str}", guards=[require_read_access])
    async def get_plan(
        self,
        state: State,
        plan_id: PathId,
    ) -> Response[ApiResponse[Plan]]:
        """Get a plan by id.

        Args:
            state: Application state.
            plan_id: Plan identifier.

        Returns:
            The plan, or 404 if not found.
        """
        plan = require_resource_or_404(
            await _service(state).get(plan_id),
            resource_type="Plan",
            identifier=plan_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
        )
        return Response(content=ApiResponse[Plan](data=plan), status_code=200)

    @patch(
        "/{plan_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("plans.edit", key="user"),
        ],
    )
    async def edit_plan(
        self,
        request: Request[object, object, State],
        state: State,
        plan_id: PathId,
        data: EditPlanRequest,
    ) -> Response[ApiResponse[Plan]]:
        """Rework a plan's items, producing a new revision under review.

        Args:
            request: The incoming request.
            state: Application state.
            plan_id: Plan identifier.
            data: The revised item list plus optional structure overrides.

        Returns:
            The reworked plan.

        Raises:
            NotFoundError: No plan with ``plan_id`` exists.
            ValidationError: The revised items violate a plan invariant.
        """
        service = _service(state)
        existing = require_resource_or_404(
            await service.get(plan_id),
            resource_type="Plan",
            identifier=plan_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="update",
        )
        revised = await service.edit(
            existing,
            items=tuple(_item_from_payload(item) for item in data.items),
            task_structure=data.task_structure,
            coordination_topology=data.coordination_topology,
        )
        publish_ws_event(
            request,
            WsEventType.PLAN_UPDATED,
            CHANNEL_PLANS,
            {
                "plan_id": str(revised.id),
                "version": revised.version,
                "status": revised.status.value,
            },
        )
        return Response(content=ApiResponse[Plan](data=revised), status_code=200)

    @post(
        "/{plan_id:str}/request-changes",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("plans.request_changes", key="user"),
        ],
    )
    async def request_changes(
        self,
        request: Request[object, object, State],
        state: State,
        plan_id: PathId,
        data: RequestPlanChangesRequest,
    ) -> Response[ApiResponse[Plan]]:
        """Send a plan back to the org for revision, with a note.

        Args:
            request: The incoming request.
            state: Application state.
            plan_id: Plan identifier.
            data: The requested-changes note.

        Returns:
            The plan, now back in draft.

        Raises:
            NotFoundError: No plan with ``plan_id`` exists.
        """
        service = _service(state)
        existing = require_resource_or_404(
            await service.get(plan_id),
            resource_type="Plan",
            identifier=plan_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="update",
        )
        drafted = await service.request_changes(existing, note=data.note)
        publish_ws_event(
            request,
            WsEventType.PLAN_CHANGES_REQUESTED,
            CHANNEL_PLANS,
            {
                "plan_id": str(drafted.id),
                "status": drafted.status.value,
                "note": data.note,
            },
        )
        return Response(content=ApiResponse[Plan](data=drafted), status_code=200)


def _parse_status(status: NotBlankStr | None) -> PlanStatus | None:
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
        raise ValidationError(msg) from exc
