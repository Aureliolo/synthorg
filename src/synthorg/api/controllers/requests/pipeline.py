"""Background intake-pipeline reconciliation for approved requests.

Drives an APPROVED ``ClientRequest`` through the work-entry adapter
(pipeline spine) off the request lock, then re-acquires the lock to
write the terminal state -- respecting any terminal status reached
meanwhile (a concurrent reject wins over a late pipeline success).
``process_intake_pipeline`` is the public entry point spawned by
``RequestController.approve_request`` via ``_spawn_intake_pipeline``.
"""

import asyncio
import functools
from collections.abc import Awaitable, Callable

from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.client.models import ClientRequest, RequestStatus
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.pipeline.errors import WorkIntakeRejectedError
from synthorg.engine.state import intake_entry_adapter_of
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

# Best-effort WS publisher bound to the originating request. The
# background reconciliation runs after the HTTP response is sent, so
# the closure keeps a reference to the request only to reach the
# channels plugin; ``publish_ws_event`` is itself best-effort.
_Publisher = Callable[[WsEventType, ClientRequest], None]


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
