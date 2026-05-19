"""Client request lifecycle endpoints at /requests."""

import asyncio
from collections.abc import Callable
from typing import Annotated, Any, Final

from litestar import Controller, Request, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.channels import CHANNEL_REQUESTS, publish_ws_event
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.api.ws_models import WsEventType
from synthorg.client.models import (
    ClientRequest,
    RequestStatus,
    TaskRequirement,
)
from synthorg.client.simulation_state import ClientSimulationState  # noqa: TC001
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    NotFoundError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.pipeline.errors import WorkIntakeRejectedError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.client import (
    CLIENT_REQUEST_INTAKE_PIPELINE_FAILED,
    CLIENT_REQUEST_STATUS_TRANSITIONED,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50

# Best-effort WS publisher bound to the originating request. The
# background reconciliation runs after the HTTP response is sent, so
# the closure keeps a reference to the request only to reach the
# channels plugin; ``publish_ws_event`` is itself best-effort.
_Publisher = Callable[[WsEventType, ClientRequest], None]


class CreateRequestPayload(BaseModel):
    """Request payload for submitting a new client request."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    client_id: NotBlankStr = Field(description="Requesting client id")
    requirement: TaskRequirement = Field(description="Task requirement")


class RejectionPayload(BaseModel):
    """Payload carrying a rejection reason."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    reason: NotBlankStr = Field(description="Reason for rejection")


class ScopingPayload(BaseModel):
    """Payload carrying scoping notes and an optional refined requirement."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    notes: NotBlankStr = Field(description="Scoping notes from the reviewer")
    refined_title: NotBlankStr | None = Field(default=None)
    refined_description: NotBlankStr | None = Field(default=None)
    refined_acceptance_criteria: tuple[NotBlankStr, ...] | None = Field(
        default=None,
    )


def _publish(
    request: Request[Any, Any, Any],
    event_type: WsEventType,
    client_request: ClientRequest,
) -> None:
    """Best-effort publish a request lifecycle event."""
    publish_ws_event(
        request,
        event_type,
        CHANNEL_REQUESTS,
        {
            "request_id": client_request.request_id,
            "client_id": client_request.client_id,
            "status": client_request.status.value,
        },
    )


def _walk_to_approved(stored: ClientRequest) -> ClientRequest:
    """Walk a submitted/scoped request to ``APPROVED``.

    ``SUBMITTED`` traverses ``TRIAGING -> SCOPING -> APPROVED``; an
    already-``SCOPING`` request (manual scope flow) goes straight to
    ``APPROVED``. ``with_status`` carries metadata forward, so the
    refined requirement and ``scoping_notes`` survive into the work
    item the adapter builds.
    """
    walked = stored
    if walked.status is RequestStatus.SUBMITTED:
        walked = walked.with_status(RequestStatus.TRIAGING)
        walked = walked.with_status(RequestStatus.SCOPING)
    return walked.with_status(RequestStatus.APPROVED)


def _spawn_intake_pipeline(
    *,
    app_state: AppState,
    sim_state: ClientSimulationState,
    request_id: str,
    publish: _Publisher,
) -> None:
    """Spawn + track the background intake-pipeline reconciliation.

    Mirrors the simulations runner's callback ordering: the exception
    logger is attached before the set-discard so a fast-completing
    failure still surfaces, and a strong reference is held in
    ``sim_state.background_tasks`` so the task is not GC'd mid-flight.
    """
    task = asyncio.create_task(
        process_intake_pipeline(
            app_state=app_state,
            sim_state=sim_state,
            request_id=request_id,
            publish=publish,
        )
    )
    task.add_done_callback(
        log_task_exceptions(
            logger,
            CLIENT_REQUEST_INTAKE_PIPELINE_FAILED,
            request_id=request_id,
        ),
    )
    task.add_done_callback(sim_state.background_tasks.discard)
    sim_state.background_tasks.add(task)


async def process_intake_pipeline(
    *,
    app_state: AppState,
    sim_state: ClientSimulationState,
    request_id: str,
    publish: _Publisher | None = None,
) -> None:
    """Drive an approved request through the work pipeline and reconcile.

    Runs the work-entry adapter (pipeline spine) WITHOUT holding the
    request lock -- agent execution can take minutes and a concurrent
    reject must still be able to cancel an in-flight approval. The
    terminal write re-acquires the lock and respects any terminal
    status reached meanwhile (a user reject wins over a late pipeline
    success). Adapter / pipeline failures are logged and the request
    is cancelled with the reason; ``MemoryError`` / ``RecursionError``
    propagate.
    """
    async with app_state.acquire_request_lock(request_id):
        try:
            approved = await sim_state.request_store.get(request_id)
        except KeyError:
            logger.warning(
                CLIENT_REQUEST_INTAKE_PIPELINE_FAILED,
                request_id=request_id,
                note="request vanished before pipeline start",
            )
            return
        if approved.status is not RequestStatus.APPROVED:
            logger.info(
                CLIENT_REQUEST_STATUS_TRANSITIONED,
                request_id=request_id,
                from_status=approved.status.value,
                to_status=approved.status.value,
                note="pipeline skipped: request no longer APPROVED",
            )
            return
    try:
        result = await app_state.intake_entry_adapter.submit(approved)
    except MemoryError, RecursionError:
        raise
    except WorkIntakeRejectedError as exc:
        # Intake declining the work is a normal outcome, not a defect.
        await _reconcile_cancel(
            sim_state,
            app_state,
            request_id,
            reason=safe_error_description(exc),
            publish=publish,
        )
        return
    except Exception as exc:
        logger.error(
            CLIENT_REQUEST_INTAKE_PIPELINE_FAILED,
            request_id=request_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        await _reconcile_cancel(
            sim_state,
            app_state,
            request_id,
            reason=safe_error_description(exc),
            publish=publish,
        )
        return
    await _reconcile_success(
        sim_state,
        app_state,
        request_id,
        task_id=result.task_id,
        publish=publish,
    )


async def _reconcile_success(
    sim_state: ClientSimulationState,
    app_state: AppState,
    request_id: str,
    *,
    task_id: str,
    publish: _Publisher | None,
) -> None:
    """Walk the request to ``TASK_CREATED`` unless already terminal."""
    async with app_state.acquire_request_lock(request_id):
        current = await _current_if_approved(sim_state, request_id)
        if current is None:
            return
        metadata = dict(current.metadata)
        metadata["task_id"] = task_id
        created = current.with_status(
            RequestStatus.TASK_CREATED,
            metadata=metadata,
        )
        await sim_state.request_store.save(created)
        logger.info(
            CLIENT_REQUEST_STATUS_TRANSITIONED,
            request_id=created.request_id,
            client_id=created.client_id,
            from_status=RequestStatus.APPROVED.value,
            to_status=created.status.value,
        )
        if publish is not None:
            publish(WsEventType.REQUEST_APPROVED, created)
    app_state.release_request_lock_if_idle(request_id)


async def _reconcile_cancel(
    sim_state: ClientSimulationState,
    app_state: AppState,
    request_id: str,
    *,
    reason: str,
    publish: _Publisher | None,
) -> None:
    """Cancel the request with ``reason`` unless already terminal."""
    async with app_state.acquire_request_lock(request_id):
        current = await _current_if_approved(sim_state, request_id)
        if current is None:
            return
        metadata = dict(current.metadata)
        metadata["rejection_reason"] = reason
        cancelled = current.with_status(
            RequestStatus.CANCELLED,
            metadata=metadata,
        )
        await sim_state.request_store.save(cancelled)
        logger.info(
            CLIENT_REQUEST_STATUS_TRANSITIONED,
            request_id=cancelled.request_id,
            client_id=cancelled.client_id,
            from_status=RequestStatus.APPROVED.value,
            to_status=cancelled.status.value,
            reason=reason,
        )
        if publish is not None:
            publish(WsEventType.REQUEST_REJECTED, cancelled)
    app_state.release_request_lock_if_idle(request_id)


async def _current_if_approved(
    sim_state: ClientSimulationState,
    request_id: str,
) -> ClientRequest | None:
    """Return the stored request iff still ``APPROVED``, else ``None``.

    A ``None`` return means a concurrent reject (or a redelivered
    reconciliation) already drove the request to a terminal state;
    the caller must not override it.
    """
    try:
        current = await sim_state.request_store.get(request_id)
    except KeyError:
        logger.warning(
            CLIENT_REQUEST_INTAKE_PIPELINE_FAILED,
            request_id=request_id,
            note="request vanished before reconciliation",
        )
        return None
    if current.status is not RequestStatus.APPROVED:
        logger.info(
            CLIENT_REQUEST_STATUS_TRANSITIONED,
            request_id=request_id,
            from_status=current.status.value,
            to_status=current.status.value,
            note="reconciliation skipped: request already terminal",
        )
        return None
    return current


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
            Parameter(description="Filter to requests in this status."),
        ] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[ClientRequest]:
        """List stored client requests, optionally filtered by status."""
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        all_requests = await sim_state.request_store.list_all()
        if status is not None:
            all_requests = tuple(r for r in all_requests if r.status == status)
        page, meta = paginate_cursor(
            all_requests,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{request_id:str}")
    async def get_request(
        self,
        state: State,
        request_id: str,
    ) -> ApiResponse[ClientRequest]:
        """Return a single request by id."""
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        try:
            stored = await sim_state.request_store.get(request_id)
        except KeyError as exc:
            msg = f"Request {request_id!r} not found"
            raise NotFoundError(msg) from exc
        return ApiResponse(data=stored)

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
        request: Request[Any, Any, Any],
        state: State,
        data: CreateRequestPayload,
    ) -> ApiResponse[ClientRequest]:
        """Persist a new ``ClientRequest`` in SUBMITTED status."""
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
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
        _publish(request, WsEventType.REQUEST_SUBMITTED, client_request)
        return ApiResponse(data=client_request)

    @post(
        "/{request_id:str}/scope",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("requests.update_scope", key="user"),
        ],
    )
    async def scope_request(
        self,
        request: Request[Any, Any, Any],
        state: State,
        request_id: str,
        data: ScopingPayload,
    ) -> ApiResponse[ClientRequest]:
        """Walk a request into SCOPING status with scoping notes.

        Accepts requests in ``SUBMITTED`` (walked through
        ``TRIAGING``) or ``TRIAGING`` state. Rejects any other
        source status with a 409.

        Raises:
            NotFoundError: If the request is not known.
            ConflictError: If the request is not in a scopable state.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        # Serialise lifecycle transitions per request id so two
        # concurrent ``scope`` / ``approve`` / ``reject`` calls for the
        # same request cannot both pass the status precondition and
        # race at ``save`` time.  The lock scope is intentionally
        # narrow -- only the get/check/save critical section -- so a
        # stuck request does not block unrelated requests.
        async with app_state.acquire_request_lock(request_id):
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
            overrides: dict[str, Any] = {}
            if (
                data.refined_title is not None
                or data.refined_description is not None
                or data.refined_acceptance_criteria is not None
            ):
                overrides["requirement"] = requirement.model_copy(
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
            scoped = walked.with_status(
                RequestStatus.SCOPING,
                metadata=metadata,
                **overrides,
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
            _publish(request, WsEventType.REQUEST_SCOPED, scoped)
        return ApiResponse(data=scoped)

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
        request: Request[Any, Any, Any],
        state: State,
        request_id: str,
    ) -> ApiResponse[ClientRequest]:
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
            AgentRuntimeNotConfiguredError: If no work-entry adapter
                is wired (empty company / no provider): the request
                stays approvable once a provider is configured.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        async with app_state.acquire_request_lock(request_id):
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
            if not app_state.has_intake_entry_adapter:
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
            _publish(request, WsEventType.REQUEST_APPROVED, approved)
            _spawn_intake_pipeline(
                app_state=app_state,
                sim_state=sim_state,
                request_id=request_id,
                publish=lambda et, cr: _publish(request, et, cr),
            )
        # APPROVED is not terminal: the background reconciliation drops
        # the lock once it reaches TASK_CREATED / CANCELLED.
        return ApiResponse(data=approved)

    @post(
        "/{request_id:str}/reject",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("requests.reject", key="user"),
        ],
    )
    async def reject_request(
        self,
        request: Request[Any, Any, Any],
        state: State,
        request_id: str,
        data: RejectionPayload,
    ) -> ApiResponse[ClientRequest]:
        """Cancel a request, recording the rejection reason."""
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        async with app_state.acquire_request_lock(request_id):
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
            _publish(request, WsEventType.REQUEST_REJECTED, cancelled)
        # Reject walks to ``CANCELLED`` (terminal) -- drop the lock.
        app_state.release_request_lock_if_idle(request_id)
        return ApiResponse(data=cancelled)
