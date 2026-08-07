"""Plan controller -- read, rework, and request-changes on durable plans.

The first-class surface for the plan-review workspace. Approve / reject stay
on the ``/approvals`` decision endpoints (the workspace holds the linked
``approval_id``): those drive the single, idempotent decision + dispatch path,
so this controller deliberately does not duplicate them. It owns the
plan-native capabilities the approval flow lacks -- reading the durable plan,
reworking its items, and sending it back for revision.
"""

from typing import Annotated, Final

from litestar import Controller, Request, Response, delete, get, patch, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.channels import (
    CHANNEL_PLANS,
    plan_updated_payload,
    publish_ws_event,
)
from synthorg.api.controllers._plan_approval_retire import retire_review_approval
from synthorg.api.controllers._plan_replan import (
    RevisionInputs,
    reject_unroutable_owners,
    replan_initiative,
)
from synthorg.api.controllers._requester import extract_requester
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_plans import (
    EditPlanRequest,
    PlanEvaluationAttempt,
    PlanEvaluationResponse,
    PlanItemPayload,
    ReplanRequest,
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
from synthorg.api.services.plan_evaluation_service import PlanEvaluationService
from synthorg.api.services.plan_service import PlanService
from synthorg.api.ws_models import WsEventType
from synthorg.core.domain_errors import ValidationError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_RESOURCE_NOT_FOUND,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

_DEFAULT_LIMIT: Final[int] = 50


def _service(state: State) -> PlanService:
    """Build the per-request :class:`PlanService` instance.

    Returns:
        ``PlanService`` bound to this backend's plan store.
    """
    persistence = persistence_of(state.app_state)
    return PlanService(repo=persistence.plans, clock=state.app_state.clock)


def _evaluation_service(state: State) -> PlanEvaluationService:
    """Build the per-request judgement reader.

    Returns:
        ``PlanEvaluationService`` bound to this backend's judgement store.
    """
    return PlanEvaluationService(
        reports=persistence_of(state.app_state).evaluation_reports
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
        satisfies=payload.satisfies,
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
    async def list_plans(
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

    @get("/{plan_id:str}/evaluation", guards=[require_read_access])
    async def get_plan_evaluation(
        self,
        state: State,
        plan_id: PathId,
    ) -> Response[ApiResponse[PlanEvaluationResponse]]:
        """Get the evaluate stage's judgement history for a plan.

        The verdict is what decides whether an initiative delivered, so a
        parked plan can explain itself: which criteria failed, with the
        judge's evidence, per attempt.

        Args:
            state: Application state.
            plan_id: Plan identifier.

        Returns:
            The recorded judgements, newest first, or 404 if no such plan.
        """
        service = _service(state)
        require_resource_or_404(
            await service.get(plan_id),
            resource_type="Plan",
            identifier=plan_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
        )
        records = await _evaluation_service(state).history(plan_id)
        payload = PlanEvaluationResponse(
            plan_id=plan_id,
            attempts=tuple(
                PlanEvaluationAttempt(
                    attempt=record.attempt,
                    summary=record.summary,
                    verdicts=record.verdicts,
                    objective_met=record.objective_met,
                    evaluated_at=record.evaluated_at,
                )
                for record in records
            ),
        )
        return Response(
            content=ApiResponse[PlanEvaluationResponse](data=payload),
            status_code=200,
        )

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
            ValidationError: The revised items violate a plan invariant, or
                an item names an owning role the org does not staff.
        """
        service = _service(state)
        existing = require_resource_or_404(
            await service.get(plan_id),
            resource_type="Plan",
            identifier=plan_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="update",
        )
        items = tuple(_item_from_payload(item) for item in data.items)
        await reject_unroutable_owners(state.app_state, items)
        revised = await service.edit(
            existing,
            items=items,
            task_structure=data.task_structure,
            coordination_topology=data.coordination_topology,
        )
        publish_ws_event(
            request,
            WsEventType.PLAN_UPDATED,
            CHANNEL_PLANS,
            plan_updated_payload(revised),
        )
        return Response(content=ApiResponse[Plan](data=revised), status_code=200)

    @post(
        "/{plan_id:str}/replan",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("plans.replan", key="user"),
        ],
    )
    async def replan(
        self,
        request: Request[object, object, State],
        state: State,
        plan_id: PathId,
        data: ReplanRequest,
    ) -> Response[ApiResponse[Plan]]:
        """Revise a dispatched plan, retiring it in favour of a successor.

        Args:
            request: The incoming request.
            state: Application state.
            plan_id: Plan identifier.
            data: The revised item list plus optional structure overrides.

        Returns:
            The successor plan, awaiting review.

        Raises:
            NotFoundError: No plan with ``plan_id`` exists.
            ConflictError: The plan is not dispatched, so it is edited instead.
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
        successor = await replan_initiative(
            state.app_state,
            existing,
            revision=RevisionInputs(
                items=tuple(_item_from_payload(item) for item in data.items),
                task_structure=data.task_structure,
                coordination_topology=data.coordination_topology,
            ),
            requested_by=extract_requester(state),
        )
        publish_ws_event(
            request,
            WsEventType.PLAN_UPDATED,
            CHANNEL_PLANS,
            plan_updated_payload(successor, supersedes=existing),
        )
        return Response(content=ApiResponse[Plan](data=successor), status_code=201)

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

    @delete(
        "/{plan_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("plans.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_plan(
        self,
        request: Request[object, object, State],
        state: State,
        plan_id: PathId,
    ) -> None:
        """Remove a plan that is not building.

        Without this route a plan whose parent task is gone, or whose
        project is being cleaned up, had no way out at all: it stayed in
        the review queue asking for a decision on work with no owner, and
        the task holding it could not be deleted either.

        Args:
            request: The incoming request.
            state: Application state.
            plan_id: Plan identifier.

        Raises:
            NotFoundError: No plan with ``plan_id`` exists.
            PlanNotDeletableError: The plan is dispatched or already decided.
            ConflictError: The parked review approval was decided while the
                delete was being prepared, so the plan is still being acted
                on. Nothing is removed and the operator retries.
        """
        service = _service(state)
        existing = require_resource_or_404(
            await service.get(plan_id),
            resource_type="Plan",
            identifier=plan_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="delete",
        )
        # Ahead of the delete, and gating it: an approval left pending would
        # drive the resume path at a missing plan, and retiring it afterwards
        # has no recovery if the write does not land.
        await retire_review_approval(state.app_state, existing)
        await service.delete(existing, requested_by=extract_requester(state))
        # The review inbox and any open detail view drop it on the same
        # event every other plan mutation publishes.
        publish_ws_event(
            request,
            WsEventType.PLAN_UPDATED,
            CHANNEL_PLANS,
            plan_updated_payload(existing),
        )


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
