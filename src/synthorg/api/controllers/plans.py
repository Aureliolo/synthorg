"""Plan controller -- read, rework, and request-changes on durable plans.

The first-class surface for the plan-review workspace. Approve / reject stay
on the ``/approvals`` decision endpoints (the workspace holds the linked
``approval_id``): those drive the single, idempotent decision + dispatch path,
so this controller deliberately does not duplicate them. It owns the
plan-native capabilities the approval flow lacks -- reading the durable plan,
reworking its items, and sending it back for revision.
"""

from typing import Final

from litestar import Controller, Request, Response, delete, get, patch, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api._read_names import agent_name_map
from synthorg.api.channels import (
    CHANNEL_PLANS,
    plan_updated_payload,
    publish_ws_event,
)
from synthorg.api.controllers._bulk_delete import (
    BulkDeleteRequest,
    BulkDeleteResult,
)
from synthorg.api.controllers._plan_filters import (
    PlanObjectiveFilter,
    PlanProjectFilter,
    PlanStatusFilter,
)
from synthorg.api.controllers._plan_input_validation import (
    reject_undecidable_graph,
    reject_unroutable_owners,
)
from synthorg.api.controllers._plan_removal import remove_plan, remove_plans
from synthorg.api.controllers._plan_replan import (
    RevisionInputs,
    replan_initiative,
)
from synthorg.api.controllers._plan_rework import replan_for_change_request
from synthorg.api.controllers._plan_rows import plan_page, plan_row
from synthorg.api.controllers._plan_translation import (
    item_from_payload,
    parse_status,
)
from synthorg.api.controllers._requester import extract_requester
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_named_rows import LifecycleTransitionRow, PlanRow
from synthorg.api.dto_plans import (
    EditPlanRequest,
    PlanEvaluationAttempt,
    PlanEvaluationResponse,
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
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.services.plan_evaluation_service import PlanEvaluationService
from synthorg.api.services.plan_service import PlanService
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.ws_models import WsEventType
from synthorg.core.lifecycle_transition import LifecycleEntityKind
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_RESOURCE_NOT_FOUND,
)
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionFilterSpec,
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
    return build_plan_service(persistence, clock=state.app_state.clock)


def _evaluation_service(state: State) -> PlanEvaluationService:
    """Build the per-request judgement reader.

    Returns:
        ``PlanEvaluationService`` bound to this backend's judgement store.
    """
    return PlanEvaluationService(
        reports=persistence_of(state.app_state).evaluation_reports
    )


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
    ) -> PaginatedResponse[PlanRow]:
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
        parsed_status = parse_status(status)
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
        return PaginatedResponse[PlanRow](
            data=await plan_page(state.app_state, plans[:limit]), pagination=meta
        )

    @get("/{plan_id:str}", guards=[require_read_access])
    async def get_plan(
        self,
        state: State,
        plan_id: PathId,
    ) -> Response[ApiResponse[PlanRow]]:
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
        return Response(
            content=ApiResponse[PlanRow](data=await plan_row(state.app_state, plan)),
            status_code=200,
        )

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

    @get("/{plan_id:str}/transitions", guards=[require_read_access])
    async def get_plan_transitions(
        self,
        state: State,
        plan_id: PathId,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> Response[ApiResponse[tuple[LifecycleTransitionRow, ...]]]:
        """Get the durable record of how a plan reached its current status.

        The status itself says where the plan is; this says how it got there
        and who moved it, from persisted rows rather than a process's log.

        Args:
            state: Application state.
            plan_id: Plan identifier.
            limit: Maximum transitions to return, newest first.

        Returns:
            The recorded transitions, newest first, or 404 if no such plan.
        """
        require_resource_or_404(
            await _service(state).get(plan_id),
            resource_type="Plan",
            identifier=plan_id,
            log_event=API_RESOURCE_NOT_FOUND,
            operation="read",
        )
        rows = await persistence_of(state.app_state).lifecycle_transitions.query(
            LifecycleTransitionFilterSpec(
                entity_kind=LifecycleEntityKind.PLAN,
                entity_id=plan_id,
            ),
            limit=limit,
        )
        names = await agent_name_map(state.app_state)
        return Response(
            content=ApiResponse[tuple[LifecycleTransitionRow, ...]](
                data=tuple(LifecycleTransitionRow.of(row, names) for row in rows)
            ),
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
    ) -> Response[ApiResponse[PlanRow]]:
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
        items = tuple(item_from_payload(item) for item in data.items)
        await reject_unroutable_owners(state.app_state, items)
        reject_undecidable_graph(items, task_structure=data.task_structure)
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
        return Response(
            content=ApiResponse[PlanRow](data=await plan_row(state.app_state, revised)),
            status_code=200,
        )

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
    ) -> Response[ApiResponse[PlanRow]]:
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
                items=tuple(item_from_payload(item) for item in data.items),
                task_structure=data.task_structure,
                coordination_topology=data.coordination_topology,
            ),
            requested_by=extract_requester(),
        )
        publish_ws_event(
            request,
            WsEventType.PLAN_UPDATED,
            CHANNEL_PLANS,
            plan_updated_payload(successor, supersedes=existing),
        )
        return Response(
            content=ApiResponse[PlanRow](
                data=await plan_row(state.app_state, successor)
            ),
            status_code=201,
        )

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
    ) -> Response[ApiResponse[PlanRow]]:
        """Send a plan back to the org for revision, with a note.

        The org re-plans against the note before this returns, so the operator
        gets the corrected plan rather than a parked one nobody will revise.
        LLM-bound, like any other turn that asks the org to think.

        Args:
            request: The incoming request.
            state: Application state.
            plan_id: Plan identifier.
            data: The requested-changes note.

        Returns:
            The re-planned plan, back under review.

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
        replanned = await replan_for_change_request(
            state.app_state, existing, note=data.note
        )
        drafted = await service.request_changes(
            existing,
            items=replanned.items,
            premises=replanned.premises,
            note=data.note,
        )
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
        return Response(
            content=ApiResponse[PlanRow](data=await plan_row(state.app_state, drafted)),
            status_code=200,
        )

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
            PlanNotDeletableError: The plan's items are still building, or it
                is already decided.
            ConflictError: The parked review approval was decided while the
                delete was being prepared, so the plan is still being acted
                on. Nothing is removed and the operator retries.
        """
        await remove_plan(
            request,
            state,
            _service(state),
            plan_id,
            requested_by=extract_requester(),
        )

    @post(
        "/bulk-delete",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("plans.bulk_delete", key="user"),
        ],
    )
    async def bulk_delete_plans(
        self,
        request: Request[object, object, State],
        state: State,
        data: BulkDeleteRequest,
    ) -> ApiResponse[BulkDeleteResult]:
        """Delete every selected plan, reporting each row's outcome.

        A plan refuses deletion on its own terms (its items are still
        building, its review approval was just decided), and those refusals
        are the common case in a mixed selection, so each is collected against
        its own row rather than ending the action.

        Returns:
            What was removed and what remains.
        """
        result = await remove_plans(
            request,
            state,
            _service(state),
            data.ids,
            # Bound before anything is written: an unbound requester is a
            # server fault, and a fault must not first expire a plan's review
            # approval and then refuse the delete.
            requested_by=extract_requester(),
        )
        return ApiResponse(data=result)
