# module-kind: code
"""Shared state + helpers for the on-startup / on-shutdown runners.

``_LifecycleTasks`` carries the mutable janitor-task / auto-wired-dispatcher /
health-prober handles that the startup runner sets and the shutdown runner
tears down (the historic ``nonlocal`` state of ``_build_lifecycle``'s closures).
The three wiring helpers are shared by both runners. Split into a leaf module so
the startup + shutdown runners and the builder import them without a cycle.
"""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.approval.state import ApprovalStateSlice
from synthorg.communication.state import CommunicationStateSlice
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import (
    API_APP_SHUTDOWN,
    API_APP_SHUTDOWN_TIMEOUT,
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.providers.health_prober import ProviderHealthProber
    from synthorg.settings.dispatcher import SettingsChangeDispatcher

logger = get_logger(__name__)


@dataclass
class _LifecycleTasks:
    """Mutable handles the startup runner sets and the shutdown runner clears.

    These were the ``nonlocal`` state of ``_build_lifecycle``'s closures; a
    shared container lets the extracted ``_run_startup`` / ``_run_shutdown``
    functions own the same state across the lifespan without a closure.
    """

    ticket_cleanup_task: asyncio.Task[None] | None = None
    audit_retention_task: asyncio.Task[None] | None = None
    webhook_cleanup_task: asyncio.Task[None] | None = None
    auto_wired_dispatcher: SettingsChangeDispatcher | None = None
    health_prober: ProviderHealthProber | None = None
    training_memory_backend: object | None = None


async def _cancel_with_timeout(
    task: asyncio.Task[None],
    *,
    service: str,
    timeout: float,  # noqa: ASYNC109 -- per-task shutdown budget
) -> None:
    """Cancel *task* and await completion with a hard timeout.

    The three janitor loops the lifecycle builder owns are wake-poll-sleep loops
    with no in-flight work, so a body that shields ``CancelledError`` is the
    only realistic way to hang here. Bound the wait to *timeout* and log at
    ERROR via ``API_APP_SHUTDOWN_TIMEOUT`` when the budget elapses; downstream
    service-teardown still runs.

    ``CancelledError`` is the normal completion path and is suppressed.
    ``MemoryError`` / ``RecursionError`` are re-raised because they must surface.

    Args:
        task: The janitor task to cancel.
        service: Service label for the structured shutdown log.
        timeout: Hard cancel-and-await budget (seconds).

    Raises:
        MemoryError: Re-raised unchanged.
        RecursionError: Re-raised unchanged.
    """
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.CancelledError:
        return
    except MemoryError, RecursionError:
        raise
    except TimeoutError as exc:
        log_exception_redacted(
            logger,
            API_APP_SHUTDOWN_TIMEOUT,
            exc,
            service=service,
            timeout_seconds=timeout,
            context=f"Failed to cancel {service} within shutdown budget",
        )
    except Exception as exc:  # noqa: BLE001 -- janitor best-effort: log and continue
        # A janitor task that fails with a non-timeout exception must not be
        # silently swallowed -- log at ERROR via the shutdown event so the
        # operator sees the underlying cause, then continue with downstream
        # services (the helper's contract is "never block the whole shutdown
        # window").
        log_exception_redacted(
            logger,
            API_APP_SHUTDOWN,
            exc,
            service=service,
            phase="task_cancellation",
            context=f"{service} task crashed during cancellation",
        )


async def _wire_workflow_observer(
    task_engine: object,
    persistence: PersistenceBackend,
    app_state: AppState,
) -> None:
    """Register WorkflowExecutionObserver on `task_engine` once.

    Must be called after the SettingsService auto-wire so `config_resolver`
    drives `max_subworkflow_depth`. Falls back to the seed default (with INFO
    log) when no resolver is wired, so executions still advance.
    """
    if task_engine is None or persistence is None:
        return
    if not (
        hasattr(persistence, "workflow_definitions")
        and hasattr(persistence, "workflow_executions")
    ):
        return
    from synthorg.engine.workflow.execution_observer import (  # noqa: PLC0415
        WorkflowExecutionObserver,
    )

    if any(
        isinstance(o, WorkflowExecutionObserver)
        for o in getattr(task_engine, "_observers", ())
    ):
        return
    if app_state.slice(SettingsStateSlice).config_resolver is not None:
        engine_bridge = await config_resolver_of(app_state).get_engine_bridge_config()
        max_depth = engine_bridge.max_subworkflow_depth
    else:
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            EngineBridgeConfig,
        )

        max_depth = EngineBridgeConfig().max_subworkflow_depth
        logger.info(
            API_APP_STARTUP,
            component="workflow_execution_observer",
            note=(
                "config_resolver not wired; registering observer with the "
                "EngineBridgeConfig seed default for max_subworkflow_depth"
            ),
            max_subworkflow_depth=max_depth,
        )
    observer = WorkflowExecutionObserver(
        definition_repo=persistence.workflow_definitions,
        execution_repo=persistence.workflow_executions,
        task_engine=task_engine,  # type: ignore[arg-type]
        max_subworkflow_depth=max_depth,
    )
    task_engine.register_observer(observer)  # type: ignore[attr-defined]


async def _wire_approval_gate(
    persistence: PersistenceBackend | None,
    app_state: AppState,
) -> None:
    """Construct the single boot ApprovalGate once persistence connects.

    One gate, shared by both governance sides: the engine parks blocked contexts
    and the ``/approvals`` controller resumes them. Park and resume must operate
    on the same gate over the same ``ParkedContextRepository``.

    Idempotent: a re-entered lifespan (shared-app test fixtures) skips when a
    gate is already wired. When persistence is absent or not connected the gate
    is still constructed (with no parked repo) so the single-gate invariant and
    the review-gate flow hold.
    """
    if app_state.slice(ApprovalStateSlice).gate is not None:
        return
    from synthorg.engine.approval_gate import ApprovalGate  # noqa: PLC0415
    from synthorg.security.timeout.park_service import (  # noqa: PLC0415
        ParkService,
    )

    parked_repo = None
    if (
        persistence is not None
        and getattr(persistence, "is_connected", False)
        and hasattr(persistence, "parked_contexts")
    ):
        parked_repo = persistence.parked_contexts
    # The boot gate bypasses the engine's _make_approval_gate(), so the
    # configured approval-interrupt timeout must be threaded in here explicitly
    # or any non-default setting is silently ignored. When the resolver is not
    # yet wired fall back to the EngineBridgeConfig seed default.
    if app_state.slice(SettingsStateSlice).config_resolver is not None:
        engine_bridge = await config_resolver_of(app_state).get_engine_bridge_config()
        interrupt_timeout = engine_bridge.approval_interrupt_timeout_seconds
    else:
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            EngineBridgeConfig,
        )

        interrupt_timeout = EngineBridgeConfig().approval_interrupt_timeout_seconds
    communication = app_state.slice(CommunicationStateSlice)
    gate = ApprovalGate(
        park_service=ParkService(),
        parked_context_repo=parked_repo,
        notification_dispatcher=app_state.slice(NotificationsStateSlice).dispatcher,
        event_hub=communication.event_stream_hub,
        interrupt_store=communication.interrupt_store,
        interrupt_timeout_seconds=interrupt_timeout,
    )
    app_state.swap_slice(
        app_state.slice(ApprovalStateSlice).model_copy(update={"gate": gate})
    )
    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="approval_gate",
        has_parked_context_repo=parked_repo is not None,
    )
