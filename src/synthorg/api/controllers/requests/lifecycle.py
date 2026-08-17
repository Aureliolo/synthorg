# module-kind: controller
"""Client request lifecycle endpoints at /requests."""

from typing import Annotated, Final

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api._feature_gate import ensure_feature_enabled
from synthorg.api.controllers.requests._events import publish_request_event
from synthorg.api.controllers.requests._payloads import (
    CreateRequestPayload,
    RejectionPayload,
    ScopingPayload,
)
from synthorg.api.controllers.requests._rows import ClientRequestRow, client_names
from synthorg.api.controllers.requests.pipeline import _spawn_intake_pipeline
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.client.models import ClientRequest, RequestStatus, TaskRequirement
from synthorg.client.state import client_simulation_state_of
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    NotFoundError,
)
from synthorg.engine.state import EngineStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.observability.events.client import CLIENT_REQUEST_STATUS_TRANSITIONED

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


def _walk_to_approved(stored: ClientRequest) -> ClientRequest:
    """Walk a submitted/scoped request to ``APPROVED``.

    ``SUBMITTED`` traverses ``TRIAGING -> SCOPING -> APPROVED``; an
    already-``SCOPING`` request (manual scope flow) goes straight to
    ``APPROVED``. ``with_status`` carries metadata forward, so the
    refined requirement and ``scoping_notes`` survive into the work
    item the adapter builds.

    Returns:
        ``ClientRequest`` instance.
    """
    walked = stored
    if walked.status is RequestStatus.SUBMITTED:
        walked = walked.with_status(RequestStatus.TRIAGING)
        walked = walked.with_status(RequestStatus.SCOPING)
    return walked.with_status(RequestStatus.APPROVED)


class RequestController(Controller):
    """Client request lifecycle endpoints."""

    path = "/requests"
    tags = ("requests",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_requests(
        self,
        state: State,
        status: Annotated[
            RequestStatus | None,
            QueryParameter(description="Filter to requests in this status."),
        ] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[ClientRequestRow]:
        """List stored client requests, optionally filtered by status.

        Returns:
            ``PaginatedResponse[ClientRequestRow]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        all_requests = await sim_state.request_store.list_all()
        if status is not None:
            all_requests = tuple(r for r in all_requests if r.status == status)
        page, meta = paginate_cursor(
            all_requests,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        # One pool read for the page, not one per row: the card renders the
        # client by name, and a browser-side lookup would print the key on the
        # first paint of every cold load.
        names = await client_names(app_state)
        rows = tuple(ClientRequestRow.of(stored, names) for stored in page)
        return PaginatedResponse(data=rows, pagination=meta)

    @get("/{request_id:str}")
    async def get_request(
        self,
        state: State,
        request_id: PathId,
    ) -> ApiResponse[ClientRequestRow]:
        """Return a single request by id.

        Returns:
            ``ApiResponse[ClientRequestRow]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        try:
            stored = await sim_state.request_store.get(request_id)
        except KeyError as exc:
            # Routine missing-resource 404: central handler logs the request
            # error; DEBUG keeps the queryable ``request_id`` without WARNING
            # noise for an expected client error.
            logger.debug(
                API_RESOURCE_NOT_FOUND,
                resource="client_request",
                request_id=request_id,
            )
            msg = f"Request {request_id!r} not found"
            raise NotFoundError(msg) from exc
        names = await client_names(app_state)
        return ApiResponse(data=ClientRequestRow.of(stored, names))

    @post(
        "/",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("requests.create", key="user"),
        ],
        status_code=201,
    )
    async def submit_request(
        self,
        request: Request[object, object, State],
        state: State,
        data: CreateRequestPayload,
    ) -> ApiResponse[ClientRequestRow]:
        """Persist a new ``ClientRequest`` in SUBMITTED status.

        Returns:
            ``ApiResponse[ClientRequestRow]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        try:
            await sim_state.pool.get_profile(data.client_id)
        except KeyError as exc:
            msg = f"Unknown client {data.client_id!r}"
            raise NotFoundError(msg) from exc
        client_request = ClientRequest(
            client_id=data.client_id,
            requirement=data.requirement,
        )
        await sim_state.request_store.save(client_request)
        logger.info(
            CLIENT_REQUEST_STATUS_TRANSITIONED,
            request_id=client_request.request_id,
            client_id=client_request.client_id,
            from_status=None,
            to_status=client_request.status.value,
        )
        publish_request_event(request, WsEventType.REQUEST_SUBMITTED, client_request)
        names = await client_names(app_state)
        return ApiResponse(data=ClientRequestRow.of(client_request, names))

    @post(
        "/{request_id:str}/scope",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("requests.update_scope", key="user"),
        ],
    )
    async def scope_request(
        self,
        request: Request[object, object, State],
        state: State,
        request_id: PathId,
        data: ScopingPayload,
    ) -> ApiResponse[ClientRequestRow]:
        """Walk a request into SCOPING status with scoping notes.

        Accepts requests in ``SUBMITTED`` (walked through
        ``TRIAGING``) or ``TRIAGING`` state. Rejects any other
        source status with a 409.

        Raises:
            NotFoundError: If the request is not known.
            ConflictError: If the request is not in a scopable state.

        Returns:
            ``ApiResponse[ClientRequestRow]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        # Serialise lifecycle transitions per request id so two
        # concurrent ``scope`` / ``approve`` / ``reject`` calls for the
        # same request cannot both pass the status precondition and
        # race at ``save`` time.  The lock scope is intentionally
        # narrow -- only the get/check/save critical section -- so a
        # stuck request does not block unrelated requests.
        async with app_state.request_locks.acquire(request_id):
            try:
                stored = await sim_state.request_store.get(request_id)
            except KeyError as exc:
                msg = f"Request {request_id!r} not found"
                raise NotFoundError(msg) from exc
            if stored.status not in {
                RequestStatus.SUBMITTED,
                RequestStatus.TRIAGING,
            }:
                msg = (
                    f"Request {request_id!r} cannot be scoped from "
                    f"status {stored.status.value!r}"
                )
                raise ConflictError(msg)
            metadata = dict(stored.metadata)
            metadata["scoping_notes"] = data.notes
            requirement = stored.requirement
            requirement_override: TaskRequirement | None = None
            if (
                data.refined_title is not None
                or data.refined_description is not None
                or data.refined_acceptance_criteria is not None
            ):
                requirement_override = requirement.model_copy(
                    update={
                        k: v
                        for k, v in {
                            "title": data.refined_title,
                            "description": data.refined_description,
                            "acceptance_criteria": data.refined_acceptance_criteria,
                        }.items()
                        if v is not None
                    },
                )
            walked = stored
            if walked.status == RequestStatus.SUBMITTED:
                walked = walked.with_status(
                    RequestStatus.TRIAGING,
                    metadata=metadata,
                )
            if requirement_override is not None:
                scoped = walked.with_status(
                    RequestStatus.SCOPING,
                    metadata=metadata,
                    requirement=requirement_override,
                )
            else:
                scoped = walked.with_status(
                    RequestStatus.SCOPING,
                    metadata=metadata,
                )
            await sim_state.request_store.save(scoped)
            logger.info(
                CLIENT_REQUEST_STATUS_TRANSITIONED,
                request_id=scoped.request_id,
                client_id=scoped.client_id,
                from_status=stored.status.value,
                to_status=scoped.status.value,
            )
            # Publish inside the lock so the save + WS event are
            # ordered atomically: a concurrent approve that takes the
            # lock after us cannot emit its own event before ours
            # reaches the bus.  SCOPING is not terminal, so the lock
            # is intentionally retained across this handler (approve
            # may run next on the same id).
            publish_request_event(request, WsEventType.REQUEST_SCOPED, scoped)
        names = await client_names(app_state)
        return ApiResponse(data=ClientRequestRow.of(scoped, names))

    @post(
        "/{request_id:str}/approve",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("requests.approve", key="user"),
        ],
        status_code=202,
    )
    async def approve_request(
        self,
        request: Request[object, object, State],
        state: State,
        request_id: PathId,
    ) -> ApiResponse[ClientRequestRow]:
        """Approve a request and run it through the work pipeline.

        Accepts requests in ``SUBMITTED`` or ``SCOPING`` (manual scope
        flow), walks them to ``APPROVED``, and spawns a background
        task that drives the work-entry adapter -> pipeline spine so a
        real agent executes the work. Returns ``202 Accepted`` with
        the ``APPROVED`` request; the terminal ``TASK_CREATED`` /
        ``CANCELLED`` state lands asynchronously (observable via
        ``GET /requests/{id}`` and the request WS channel).

        Raises:
            NotFoundError: If the request is not known.
            ConflictError: If the request cannot be approved from its
                current state.
            ServiceUnavailableError: If the client-intake door is
                disabled (``simulations.client_intake_enabled`` off, the
                default): the synthetic-client intake path is a benchmark
                surface, not a standing production door.
            AgentRuntimeNotConfiguredError: If no work-entry adapter
                is wired (empty company / no provider): the request
                stays approvable once a provider is configured.

        Returns:
            ``ApiResponse[ClientRequestRow]`` instance.
        """
        app_state: AppState = state.app_state
        # Off by default: the client-request intake path role-plays external
        # customers and is a benchmark door, gated so it never doubles as a
        # standing production front door. Read live so a Settings toggle
        # applies on the next request with no restart.
        await ensure_feature_enabled(
            app_state,
            "simulations",
            "client_intake_enabled",
            feature_label="Client-request intake",
        )
        sim_state = client_simulation_state_of(app_state)
        async with app_state.request_locks.acquire(request_id):
            try:
                stored = await sim_state.request_store.get(request_id)
            except KeyError as exc:
                msg = f"Request {request_id!r} not found"
                raise NotFoundError(msg) from exc
            if stored.status not in {
                RequestStatus.SUBMITTED,
                RequestStatus.SCOPING,
            }:
                msg = (
                    f"Request {request_id!r} cannot be approved from "
                    f"status {stored.status.value!r}"
                )
                raise ConflictError(msg)
            if app_state.slice(EngineStateSlice).intake_entry_adapter is None:
                # Empty company / no provider: the work pipeline (and
                # thus the entry adapter) is not wired. Reject clearly
                # rather than minting a task no agent will ever run.
                raise AgentRuntimeNotConfiguredError
            approved = _walk_to_approved(stored)
            await sim_state.request_store.save(approved)
            logger.info(
                CLIENT_REQUEST_STATUS_TRANSITIONED,
                request_id=approved.request_id,
                client_id=approved.client_id,
                from_status=stored.status.value,
                to_status=approved.status.value,
            )
            publish_request_event(request, WsEventType.REQUEST_APPROVED, approved)
            _spawn_intake_pipeline(
                app_state=app_state,
                sim_state=sim_state,
                request_id=request_id,
                publish=lambda et, cr: publish_request_event(request, et, cr),
            )
        # APPROVED is not terminal: the background reconciliation drops
        # the lock once it reaches TASK_CREATED / CANCELLED.
        names = await client_names(app_state)
        return ApiResponse(data=ClientRequestRow.of(approved, names))

    @post(
        "/{request_id:str}/reject",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("requests.reject", key="user"),
        ],
    )
    async def reject_request(
        self,
        request: Request[object, object, State],
        state: State,
        request_id: PathId,
        data: RejectionPayload,
    ) -> ApiResponse[ClientRequestRow]:
        """Cancel a request, recording the rejection reason.

        Returns:
            ``ApiResponse[ClientRequestRow]`` instance.

        Raises:
            ConflictError: Raised on the corresponding failure path.
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
        async with app_state.request_locks.acquire(request_id):
            try:
                stored = await sim_state.request_store.get(request_id)
            except KeyError as exc:
                msg = f"Request {request_id!r} not found"
                raise NotFoundError(msg) from exc
            if stored.status in {
                RequestStatus.TASK_CREATED,
                RequestStatus.CANCELLED,
            }:
                msg = (
                    f"Request {request_id!r} cannot be rejected from "
                    f"status {stored.status.value!r}"
                )
                raise ConflictError(msg)
            metadata = dict(stored.metadata)
            metadata["rejection_reason"] = data.reason
            cancelled = stored.with_status(
                RequestStatus.CANCELLED,
                metadata=metadata,
            )
            await sim_state.request_store.save(cancelled)
            logger.info(
                CLIENT_REQUEST_STATUS_TRANSITIONED,
                request_id=cancelled.request_id,
                client_id=cancelled.client_id,
                from_status=stored.status.value,
                to_status=cancelled.status.value,
            )
            publish_request_event(request, WsEventType.REQUEST_REJECTED, cancelled)
        # Reject walks to ``CANCELLED`` (terminal) -- drop the lock.
        app_state.request_locks.release_if_idle(request_id)
        names = await client_names(app_state)
        return ApiResponse(data=ClientRequestRow.of(cancelled, names))
