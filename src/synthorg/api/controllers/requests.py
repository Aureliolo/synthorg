"""Client request lifecycle endpoints at /requests."""

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Annotated, Final

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.channels import CHANNEL_REQUESTS, publish_ws_event
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
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import client_simulation_state_of
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    NotFoundError,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.errors import WorkIntakeRejectedError
from synthorg.engine.state import EngineStateSlice, intake_entry_adapter_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
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
    request: Request[object, object, State],
    event_type: WsEventType,
    client_request: ClientRequest,
) -> None:
    """Best-effort publish a request lifecycle event.

    ``REQUEST_TASK_CREATED`` additionally carries ``task_id`` so the
    frontend can navigate straight to the spawned task without a
    second ``GET /requests/{id}``. ``_reconcile_success`` always stamps
    ``metadata["task_id"]`` before publishing this event, so the field
    is contract-required for that event type and contract-absent for
    every other request lifecycle event.
    """
    payload: dict[str, object] = {
        "request_id": client_request.request_id,
        "client_id": client_request.client_id,
        "status": client_request.status.value,
    }
    if event_type is WsEventType.REQUEST_TASK_CREATED:
        task_id = client_request.metadata.get("task_id")
        if isinstance(task_id, str) and task_id:
            payload["task_id"] = task_id
    publish_ws_event(request, event_type, CHANNEL_REQUESTS, payload)


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


def _spawn_intake_pipeline(
    *,
    app_state: AppState,
    sim_state: ClientSimulationState,
    request_id: str,
    publish: _Publisher,
) -> None:
    """Spawn + track the background intake-pipeline reconciliation.

    A detached task (not a ``TaskGroup``) is correct here: the approve
    handler returns ``202`` immediately and the pipeline run outlives
    that scope by design, so there is no parent scope to await it.
    Lifecycle is tracked the same way the simulations runner tracks
    its detached runners: a strong reference in
    ``sim_state.background_tasks`` keeps the task from being GC'd
    mid-flight, the exception logger is attached before the
    set-discard so a fast-completing failure still surfaces, and the
    reference is added synchronously here (no ``await`` between
    ``create_task`` and ``add``, so a done-callback cannot run before
    the reference exists). The request lock is intentionally not held
    across the (minutes-long) pipeline run; the ``_current_if_approved``
    guard plus the reconcile error handling make a concurrent reject
    racing a late pipeline result a benign no-op rather than a lost
    transition.
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


async def _approved_or_none(
    app_state: AppState,
    sim_state: ClientSimulationState,
    request_id: str,
) -> ClientRequest | None:
    """Return the request iff still ``APPROVED``, else ``None``.

    Brief read+gate under the per-request lock. The lock-registry
    entry is always evicted on early-return paths (vanished request,
    or status already past ``APPROVED``) so the dict cannot leak an
    entry per orphaned id; on the success path the entry is kept
    because the downstream reconcile helpers re-acquire the lock and
    evict it themselves. ``release_request_lock_if_idle`` sits in
    ``finally`` so it observes the Lock already released by
    ``__aexit__`` and an idle refcount (its documented contract).

    Returns:
        The ``ClientRequest`` value when present, ``None`` otherwise.
    """
    handed_off = False
    try:
        async with app_state.acquire_request_lock(request_id):
            try:
                stored = await sim_state.request_store.get(request_id)
            except KeyError:
                logger.warning(
                    CLIENT_REQUEST_INTAKE_PIPELINE_FAILED,
                    request_id=request_id,
                    note="request vanished before pipeline start",
                )
                return None
            if stored.status is not RequestStatus.APPROVED:
                logger.info(
                    CLIENT_REQUEST_STATUS_TRANSITIONED,
                    request_id=request_id,
                    from_status=stored.status.value,
                    to_status=stored.status.value,
                    note="pipeline skipped: request no longer APPROVED",
                )
                return None
            handed_off = True
            return stored
    finally:
        if not handed_off:
            app_state.release_request_lock_if_idle(request_id)


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
    propagate. The lock-registry entry is evicted on every early
    return so the dict cannot grow unbounded across vanished or
    already-terminal requests.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    approved = await _approved_or_none(app_state, sim_state, request_id)
    if approved is None:
        return
    try:
        intake_adapter = intake_entry_adapter_of(app_state)
        result = await intake_adapter.submit(approved)
    except asyncio.CancelledError:
        # Task cancelled (e.g. app shutdown): let it propagate; do not
        # convert a cancellation into a CANCELLED request.
        raise
    except Exception as exc:
        reraise_critical(exc)
        if not isinstance(exc, WorkIntakeRejectedError):
            # Intake declining the work is a normal outcome, not a
            # defect; only non-rejection paths warrant ERROR.
            log_exception_redacted(
                logger,
                CLIENT_REQUEST_INTAKE_PIPELINE_FAILED,
                exc,
                request_id=request_id,
            )
        await _safe_finalize(
            functools.partial(
                _reconcile_cancel,
                sim_state,
                app_state,
                request_id,
                reason=safe_error_description(exc),
                publish=publish,
            ),
            request_id=request_id,
            operation="reconcile_cancel",
        )
        return

    async def _cancel_after_success_failure(reason: str) -> None:
        await _safe_finalize(
            functools.partial(
                _reconcile_cancel,
                sim_state,
                app_state,
                request_id,
                reason=reason,
                publish=publish,
            ),
            request_id=request_id,
            operation="reconcile_cancel",
        )

    await _safe_finalize(
        functools.partial(
            _reconcile_success,
            sim_state,
            app_state,
            request_id,
            task_id=result.task_id,
            publish=publish,
        ),
        request_id=request_id,
        operation="reconcile_success",
        on_failure_reconcile=_cancel_after_success_failure,
    )


async def _safe_finalize(
    finalizer: Callable[[], Awaitable[None]],
    *,
    request_id: str,
    operation: str,
    on_failure_reconcile: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Run a terminal reconciliation, never leaving the request stuck.

    A reconciliation that raises (store / lock / publish failure) must
    not leave the request silently in ``APPROVED`` with only a
    done-callback WARNING. On a ``reconcile_success`` failure the caller
    supplies ``on_failure_reconcile`` so the request is still cancelled
    (reaching a terminal state); a ``reconcile_cancel`` failure passes no
    fallback and only logs ERROR (no further fallback is possible) so the
    operator has an actionable signal keyed by ``request_id``.
    ``CancelledError`` / ``MemoryError`` / ``RecursionError`` propagate
    unchanged.

    Args:
        finalizer: The bound reconciliation coroutine factory to run.
        request_id: Request id for the structured failure log.
        operation: Reconciliation label for the structured failure log.
        on_failure_reconcile: Optional fallback invoked with the failure
            reason when ``finalizer`` raises a non-critical error.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    try:
        await finalizer()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            CLIENT_REQUEST_INTAKE_PIPELINE_FAILED,
            exc,
            request_id=request_id,
            operation=operation,
        )
        if on_failure_reconcile is not None:
            await on_failure_reconcile(
                f"reconciliation failed: {safe_error_description(exc)}"
            )


async def _reconcile_success(
    sim_state: ClientSimulationState,
    app_state: AppState,
    request_id: str,
    *,
    task_id: str,
    publish: _Publisher | None,
) -> None:
    """Walk the request to ``TASK_CREATED`` unless already terminal.

    ``release_request_lock_if_idle`` runs in ``finally`` so a failure
    inside the locked section evicts the lock entry rather than leaking
    it; the failure itself propagates to :func:`_safe_finalize`.
    """
    try:
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
                publish(WsEventType.REQUEST_TASK_CREATED, created)
    finally:
        app_state.release_request_lock_if_idle(request_id)


async def _reconcile_cancel(
    sim_state: ClientSimulationState,
    app_state: AppState,
    request_id: str,
    *,
    reason: str,
    publish: _Publisher | None,
) -> None:
    """Cancel the request with ``reason`` unless already terminal.

    ``release_request_lock_if_idle`` runs in ``finally`` (see
    :func:`_reconcile_success`).
    """
    try:
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
    finally:
        app_state.release_request_lock_if_idle(request_id)


async def _current_if_approved(
    sim_state: ClientSimulationState,
    request_id: str,
) -> ClientRequest | None:
    """Return the stored request iff still ``APPROVED``, else ``None``.

    A ``None`` return means a concurrent reject (or a redelivered
    reconciliation) already drove the request to a terminal state;
    the caller must not override it.

    Returns:
        The ``ClientRequest`` value when present, ``None`` otherwise.
    """
    try:
        current = await sim_state.request_store.get(request_id)
    except KeyError:
        # The request disappearing mid-reconciliation is an invariant
        # break (no normal path deletes an APPROVED request), not a
        # routine warning: surface it at ERROR keyed by request_id.
        logger.error(
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
            QueryParameter(description="Filter to requests in this status."),
        ] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[ClientRequest]:
        """List stored client requests, optionally filtered by status.

        Returns:
            ``PaginatedResponse[ClientRequest]`` instance.
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
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{request_id:str}")
    async def get_request(
        self,
        state: State,
        request_id: PathId,
    ) -> ApiResponse[ClientRequest]:
        """Return a single request by id.

        Returns:
            ``ApiResponse[ClientRequest]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
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
        request: Request[object, object, State],
        state: State,
        data: CreateRequestPayload,
    ) -> ApiResponse[ClientRequest]:
        """Persist a new ``ClientRequest`` in SUBMITTED status.

        Returns:
            ``ApiResponse[ClientRequest]`` instance.

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
        request: Request[object, object, State],
        state: State,
        request_id: PathId,
        data: ScopingPayload,
    ) -> ApiResponse[ClientRequest]:
        """Walk a request into SCOPING status with scoping notes.

        Accepts requests in ``SUBMITTED`` (walked through
        ``TRIAGING``) or ``TRIAGING`` state. Rejects any other
        source status with a 409.

        Raises:
            NotFoundError: If the request is not known.
            ConflictError: If the request is not in a scopable state.

        Returns:
            ``ApiResponse[ClientRequest]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
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
        request: Request[object, object, State],
        state: State,
        request_id: PathId,
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

        Returns:
            ``ApiResponse[ClientRequest]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
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
        request: Request[object, object, State],
        state: State,
        request_id: PathId,
        data: RejectionPayload,
    ) -> ApiResponse[ClientRequest]:
        """Cancel a request, recording the rejection reason.

        Returns:
            ``ApiResponse[ClientRequest]`` instance.

        Raises:
            ConflictError: Raised on the corresponding failure path.
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        sim_state = client_simulation_state_of(app_state)
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
