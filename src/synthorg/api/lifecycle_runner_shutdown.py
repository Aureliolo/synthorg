# module-kind: code
"""The on-shutdown runner: ordered teardown of the lifecycle-owned services.

``_run_shutdown`` performs the ordered teardown. The janitor-task /
dispatcher / health-prober / training-backend state it tears down lives on
the shared :class:`_LifecycleTasks` container threaded in by the builder.
"""

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Final, cast

from synthorg.a2a.state import A2aStateSlice
from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.lifecycle import _safe_shutdown, _try_stop
from synthorg.api.lifecycle_runner_support import (
    _cancel_with_timeout,
    _LifecycleTasks,
    drain_simulation_background_tasks,
)
from synthorg.api.state import _ENTRY_TASK_DRAIN_GRACE_SECONDS, AppState
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.state import HrStateSlice
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.memory.state import MemoryStateSlice
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_SHUTDOWN
from synthorg.observability.state import ObservabilityStateSlice
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.workers.execution_resume import _RESUME_DRAIN_TIMEOUT_SECONDS
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)


# Per-task shutdown budgets for the three janitor loops launched by the
# lifecycle builder. These are passive wake-poll-sleep loops so 2.0s matches the
# budget already used for the meeting scheduler / settings dispatcher /
# bus-bridge in ``api/lifecycle.py``. Wrapping the cancel-and-await with
# ``asyncio.wait_for`` keeps shutdown bounded even when a task body shields
# ``CancelledError`` (third-party callees, hung I/O); the orchestrator's SIGKILL
# deadline must not slip past ``graceful_shutdown`` (75s in api/server.py).
_TICKET_CLEANUP_SHUTDOWN_SECONDS: Final[float] = 2.0
_AUDIT_RETENTION_SHUTDOWN_SECONDS: Final[float] = 2.0
_WEBHOOK_CLEANUP_SHUTDOWN_SECONDS: Final[float] = 2.0

# Outer backstop budgets for the two in-flight drains run at the top of
# shutdown. Each drain is internally bounded (its own ``asyncio.wait``
# deadline), but a drain that hangs BEFORE reaching its internal wait
# (e.g. a stuck done-callback, a hung pre-drain await) would, without an
# outer timeout, block the whole shutdown window past the orchestrator
# SIGKILL deadline. The outer budget exceeds the inner deadline by a small
# grace so the inner mechanism (which logs ``pending_count``) fires first
# and the outer is purely the backstop.
_DRAIN_OUTER_GRACE_SECONDS: Final[float] = 2.0
_RESUME_DRAIN_OUTER_SECONDS: Final[float] = (
    _RESUME_DRAIN_TIMEOUT_SECONDS + _DRAIN_OUTER_GRACE_SECONDS
)
# ReviewGate drains through a BackgroundTaskRegistry with the registry's
# 5.0s default deadline; mirror that plus the shared grace.
_REVIEW_GATE_DRAIN_OUTER_SECONDS: Final[float] = 5.0 + _DRAIN_OUTER_GRACE_SECONDS

# Outer backstop for the objective / brownfield entry-task drain. The drain
# is internally bounded by ``_ENTRY_TASK_DRAIN_GRACE_SECONDS`` (from
# api/state.py) plus the cancel-and-await of stragglers; the outer budget
# exceeds that grace by the shared backstop so a task that shields
# ``CancelledError`` cannot block the shutdown window. Reference the source
# constant so the two values can never drift apart.
_ENTRY_TASK_DRAIN_OUTER_SECONDS: Final[float] = (
    _ENTRY_TASK_DRAIN_GRACE_SECONDS + _DRAIN_OUTER_GRACE_SECONDS
)

# Outer backstop for the client-simulation pipeline-task drain. The tasks are
# tracked on ``ClientSimulationState.background_tasks`` (a plain set, no
# registry), so the drain gives them the same grace as the entry-task set plus
# the shared backstop, then cancels stragglers so a SIGTERM mid-pipeline does
# not abandon a task writing to the simulation stores.
_SIMULATION_TASK_DRAIN_OUTER_SECONDS: Final[float] = (
    _ENTRY_TASK_DRAIN_GRACE_SECONDS + _DRAIN_OUTER_GRACE_SECONDS
)

# Outer backstop for the cooperative multi-agent drain. ``initiate_shutdown``
# is internally bounded (grace 8s + cancel-propagation 5s + cleanup 2s);
# wrap it so a strategy that hangs before reaching its own deadlines cannot
# block the shutdown window. No-op (returns immediately) when no parallel
# agent tasks are registered, which is the common case.
_COOPERATIVE_SHUTDOWN_OUTER_SECONDS: Final[float] = 18.0

# Per-service stop budgets for the remaining background services. Passive
# wake-poll-sleep loops (event-stream hub, escalation sweeper/subscriber,
# org-inflection / toolsmith / model-refresh schedulers, settings + cost
# dispatchers, notification dispatcher) cancel-and-await quickly, so they
# share the 2.0s janitor budget. Services that internally drain in-flight
# work through the lifecycle-lock pattern (health probers, OAuth token
# manager, webhook event bridge) can legitimately take up to
# ``DEFAULT_DRAIN_TIMEOUT_SECONDS``, so their outer backstop exceeds the
# inner drain by the shared grace. Every stop is bounded so a hung callee
# cannot block the shutdown window past the orchestrator SIGKILL deadline.
_SERVICE_STOP_SHUTDOWN_SECONDS: Final[float] = 2.0
_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS: Final[float] = (
    DEFAULT_DRAIN_TIMEOUT_SECONDS + _DRAIN_OUTER_GRACE_SECONDS
)


async def _run_shutdown(  # noqa: PLR0913
    tasks: _LifecycleTasks,
    app_state: AppState,
    *,
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
) -> None:
    """Run the ordered on-shutdown teardown.

    Args:
        tasks: Shared mutable handles the startup runner populated.
        app_state: Application state container.
        persistence: Persistence backend (``None`` when unconfigured).
        message_bus: Internal message bus (``None`` when unconfigured).
        bridge: Message bus bridge to WebSocket channels.
        settings_dispatcher: Settings change dispatcher.
        task_engine: Centralized task state engine.
        meeting_scheduler: Meeting scheduler service.
        backup_service: Backup and restore service.
        approval_timeout_scheduler: Background approval timeout checker.

    Raises:
        MemoryError: Re-raised unchanged from the cooperative-shutdown
            guard (never swallowed).
        RecursionError: Re-raised unchanged from the cooperative-shutdown
            guard (never swallowed).
    """
    # Emit the shutdown event before any teardown step so the gate-crossing is
    # observable even if a downstream stop hangs or raises. Mirrors the
    # ``on_startup`` emission at the top of that function.
    from synthorg import __version__  # noqa: PLC0415

    logger.info(API_APP_SHUTDOWN, version=__version__)
    # Cooperatively drain in-flight multi-agent parallel tasks first: the
    # drain gate already closed when the signal arrived (so no new agent
    # tasks register), and this waits the bounded grace for registered
    # tasks to exit at a turn boundary before force-cancelling stragglers.
    # Runs before the resume / service teardown so an in-flight agent run
    # is cancelled cleanly rather than abruptly when the loop tears down.
    try:
        await asyncio.wait_for(
            app_state.shutdown_manager.initiate_shutdown(),
            timeout=_COOPERATIVE_SHUTDOWN_OUTER_SECONDS,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- shutdown best-effort: log and continue
        logger.warning(
            API_APP_SHUTDOWN,
            phase="cooperative_shutdown",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    # Drain in-flight parked-context resumes (background tasks spawned off the
    # /approvals path) before teardown so an approved resume is not silently
    # dropped mid-flight. Read the runtime slice field directly; only the
    # agent-runtime service exposes ``drain_resume_tasks`` (the no-provider
    # backstop does not), so the ``getattr`` guard stays.
    _wes = app_state.slice(RuntimeStateSlice).worker_execution_service
    _drain_resumes = getattr(_wes, "drain_resume_tasks", None)
    if callable(_drain_resumes):
        await _try_stop(
            cast("Awaitable[None]", _drain_resumes()),
            API_APP_SHUTDOWN,
            "Failed to drain in-flight parked-context resumes",
            timeout=_RESUME_DRAIN_OUTER_SECONDS,
            service="resume_drain",
        )
    # Drain in-flight gated-completion background tasks (the red-team
    # evaluation dispatched off the /approvals path) so an approved
    # completion is not cancelled mid-evaluation and stranded in IN_REVIEW.
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415

    _review_gate = app_state.slice(ApprovalStateSlice).review_gate
    if _review_gate is not None:
        await _try_stop(
            _review_gate.drain_background_tasks(),
            API_APP_SHUTDOWN,
            "Failed to drain in-flight gated-completion background tasks",
            timeout=_REVIEW_GATE_DRAIN_OUTER_SECONDS,
            service="review_gate_drain",
        )
    # Drain in-flight objective / brownfield entry-processing tasks (spawned
    # fire-and-forget off the work-entry path and tracked only in their
    # in-memory sets) so a submission mid-flight at SIGTERM unwinds cleanly
    # instead of being abandoned when the loop tears down.
    await _try_stop(
        app_state.drain_entry_background_tasks(),
        API_APP_SHUTDOWN,
        "Failed to drain in-flight objective/brownfield entry tasks",
        timeout=_ENTRY_TASK_DRAIN_OUTER_SECONDS,
        service="entry_task_drain",
    )
    # Drain in-flight client-simulation pipeline tasks (intake approval,
    # simulation runner, task-board filing) tracked only on the simulation
    # state's in-memory set, so a task mid-pipeline at SIGTERM unwinds cleanly
    # instead of being cancelled mid-write against its stores. The helper
    # no-ops when no simulation runtime is wired.
    await _try_stop(
        drain_simulation_background_tasks(app_state),
        API_APP_SHUTDOWN,
        "Failed to drain in-flight client-simulation tasks",
        timeout=_SIMULATION_TASK_DRAIN_OUTER_SECONDS,
        service="simulation_task_drain",
    )
    # Drain the SHIP-retrospective capture tail BEFORE the memory backends it
    # writes to are disconnected below, so an in-flight retrospective is not
    # stranded mid-write (or leaked as a pending task) at SIGTERM. No-op when
    # the rollup service or its retro tail is unwired.
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    _rollup_service = app_state.slice(EngineStateSlice).project_rollup_service
    if _rollup_service is not None:
        await _try_stop(
            _rollup_service.drain_retro_capture(
                timeout_sec=DEFAULT_DRAIN_TIMEOUT_SECONDS
            ),
            API_APP_SHUTDOWN,
            "Failed to drain in-flight retrospective capture tasks",
            timeout=DEFAULT_DRAIN_TIMEOUT_SECONDS + _DRAIN_OUTER_GRACE_SECONDS,
            service="ship_retro_capture_drain",
        )
    # Stop the consolidation driver before the backend it maintains, so a
    # tick in flight cannot outlive the store it writes to.
    consolidation_scheduler = app_state.slice(MemoryStateSlice).consolidation_scheduler
    if consolidation_scheduler is not None:
        app_state.swap_slice(
            app_state.slice(MemoryStateSlice).model_copy(
                update={"consolidation_scheduler": None}
            )
        )
        await _try_stop(
            cast("Awaitable[None]", consolidation_scheduler.stop()),
            API_APP_SHUTDOWN,
            "Failed to stop memory consolidation scheduler",
            timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="memory_consolidation",
        )
    # Disconnect the shared agent-memory backend. The field is cleared before
    # disconnecting so a lifespan re-entry can wire a fresh connected backend
    # (``wire_memory_backend`` is idempotent and skips when the slice is
    # already set) without a stale handle lingering on the slice.
    memory_backend = app_state.slice(MemoryStateSlice).backend
    if memory_backend is not None:
        app_state.swap_slice(
            app_state.slice(MemoryStateSlice).model_copy(update={"backend": None})
        )
        await _try_stop(
            cast("Awaitable[None]", memory_backend.disconnect()),
            API_APP_SHUTDOWN,
            "Failed to disconnect agent memory backend",
            timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="memory_backend",
        )
    # Disconnect + unwire the hybrid org-memory backend published to the memory
    # slice at startup, mirroring the training-backend teardown above: clear the
    # field before disconnecting so a lifespan re-entry can wire a fresh backend
    # (``wire_org_memory_backend`` is idempotent and skips when the slice is
    # already set) without a stale handle lingering on the slice.
    org_memory_backend = app_state.slice(MemoryStateSlice).org_memory_backend
    if org_memory_backend is not None:
        app_state.swap_slice(
            app_state.slice(MemoryStateSlice).model_copy(
                update={"org_memory_backend": None}
            )
        )
        await _try_stop(
            org_memory_backend.disconnect(),
            API_APP_SHUTDOWN,
            "Failed to disconnect org memory backend",
            timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="org_memory_backend",
        )
    if tasks.ticket_cleanup_task is not None:
        await _cancel_with_timeout(
            tasks.ticket_cleanup_task,
            service="ticket_cleanup",
            timeout=_TICKET_CLEANUP_SHUTDOWN_SECONDS,
        )
        tasks.ticket_cleanup_task = None
    if tasks.audit_retention_task is not None:
        await _cancel_with_timeout(
            tasks.audit_retention_task,
            service="audit_retention",
            timeout=_AUDIT_RETENTION_SHUTDOWN_SECONDS,
        )
        tasks.audit_retention_task = None
    if tasks.flight_recorder_retention_task is not None:
        await _cancel_with_timeout(
            tasks.flight_recorder_retention_task,
            service="flight_recorder_retention",
            timeout=_AUDIT_RETENTION_SHUTDOWN_SECONDS,
        )
        tasks.flight_recorder_retention_task = None
    if tasks.webhook_cleanup_task is not None:
        await _cancel_with_timeout(
            tasks.webhook_cleanup_task,
            service="webhook_cleanup",
            timeout=_WEBHOOK_CLEANUP_SHUTDOWN_SECONDS,
        )
        tasks.webhook_cleanup_task = None
    communication = app_state.slice(CommunicationStateSlice)
    integrations = app_state.slice(IntegrationsStateSlice)
    if communication.event_stream_hub is not None:
        await _try_stop(
            communication.event_stream_hub.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop event stream hub",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="event_stream_hub",
        )
    if tasks.health_prober is not None:
        await _try_stop(
            tasks.health_prober.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop health prober",
            timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="health_prober",
        )
        tasks.health_prober = None
    # Stop integration background services (reverse start order).
    if communication.escalation_notify_subscriber is not None:
        await _try_stop(
            communication.escalation_notify_subscriber.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop escalation notify subscriber",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="escalation_notify_subscriber",
        )
    if communication.escalation_sweeper is not None:
        await _try_stop(
            communication.escalation_sweeper.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop escalation sweeper",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="escalation_sweeper",
        )
    # Cancel any unresolved pending futures so coroutines awaiting operator
    # decisions get a clean CancelledError (instead of hanging past shutdown)
    # and the registry map is emptied.
    if communication.escalation_registry is not None:
        await _try_stop(
            communication.escalation_registry.close(),
            API_APP_SHUTDOWN,
            "Failed to close escalation pending-futures registry",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="escalation_registry",
        )
    # The three integration draining services (OAuth token manager,
    # integration health prober, webhook event bridge) are independent
    # background loops with no inter-stop ordering dependency, so they drain
    # concurrently: the aggregate wall-clock is one drain budget rather than
    # three, keeping the worst case inside the SIGKILL deadline. Each retains
    # its own bounded ``_try_stop`` timeout.
    _integration_draining_stops: list[Coroutine[object, object, bool]] = []
    if integrations.oauth_token_manager is not None:
        _integration_draining_stops.append(
            _try_stop(
                integrations.oauth_token_manager.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop OAuth token manager",
                timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
                service="oauth_token_manager",
            )
        )
    if integrations.health_prober_service is not None:
        _integration_draining_stops.append(
            _try_stop(
                integrations.health_prober_service.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop integration health prober",
                timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
                service="integration_health_prober",
            )
        )
    if integrations.webhook_event_bridge is not None:
        _integration_draining_stops.append(
            _try_stop(
                integrations.webhook_event_bridge.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop webhook event bridge",
                timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
                service="webhook_event_bridge",
            )
        )
    if _integration_draining_stops:
        # Structured fan-out/fan-in (project convention prefers ``TaskGroup``
        # over ``gather``). ``_try_stop`` swallows its own failures and returns
        # a bool, so no child task raises -- the group's first-exception
        # cancellation never fires and all three drains run to completion.
        async with asyncio.TaskGroup() as _drain_tg:
            for _stop_coro in _integration_draining_stops:
                _ = _drain_tg.create_task(_stop_coro)
    if integrations.tunnel_provider is not None:
        await _try_stop(
            integrations.tunnel_provider.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop tunnel provider",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="tunnel_provider",
        )
    if integrations.mcp_bridge_factory is not None:
        await _try_stop(
            integrations.mcp_bridge_factory.shutdown(),
            API_APP_SHUTDOWN,
            "Failed to stop external MCP bridge factory",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="mcp_bridge_factory",
        )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    meta_slice = app_state.slice(MetaStateSlice)
    if meta_slice.org_inflection_monitor is not None:
        await _try_stop(
            meta_slice.org_inflection_monitor.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop org inflection monitor",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="org_inflection_monitor",
        )
        app_state.wire(MetaStateSlice, org_inflection_monitor=None)

    from synthorg.meta.toolsmith.state import ToolsmithStateSlice  # noqa: PLC0415

    toolsmith = app_state.slice(ToolsmithStateSlice)
    if toolsmith.cycle_scheduler is not None:
        await _try_stop(
            toolsmith.cycle_scheduler.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop toolsmith cycle scheduler",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="toolsmith_cycle_scheduler",
        )
        # Clear the service too, not just the scheduler: wire_toolsmith
        # short-circuits when service is already set, so leaving it populated
        # would skip re-wiring on the next lifespan entry (hot-reload / tests).
        app_state.swap_slice(
            toolsmith.model_copy(update={"service": None, "cycle_scheduler": None}),
        )

    from synthorg.providers.management.refresh_state import (  # noqa: PLC0415
        ModelRefreshStateSlice,
    )

    model_refresh = app_state.slice(ModelRefreshStateSlice)
    if model_refresh.service is not None:
        # ``manual_only`` mode wires a service with no scheduler, so gate the
        # teardown on ``service`` (not ``scheduler``): otherwise the stale
        # service survives into the next lifespan and wire_model_refresh's
        # idempotency guard short-circuits, leaking the prior DB handle.
        if model_refresh.scheduler is not None:
            await _try_stop(
                model_refresh.scheduler.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop model-refresh scheduler",
                timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
                service="model_refresh_scheduler",
            )
        # Clear service + scheduler so wire_model_refresh re-wires on the
        # next lifespan entry (its idempotency guard checks ``service``).
        app_state.swap_slice(
            model_refresh.model_copy(
                update={"service": None, "scheduler": None},
            ),
        )

    from synthorg.providers.tool_call_feedback.sink import (  # noqa: PLC0415
        uninstall_tool_call_signal_sink,
    )
    from synthorg.providers.tool_call_feedback.state import (  # noqa: PLC0415
        ToolCallFeedbackStateSlice,
    )

    tool_call_feedback = app_state.slice(ToolCallFeedbackStateSlice)
    # The sink is process-global and can be installed without the slice ever
    # holding a tracker, so uninstall it unconditionally (the call is a safe
    # no-op when absent). This stops the provider boundary routing into a
    # tracker whose DB handle is about to be disconnected, even on the path
    # where the slice tracker was never published.
    uninstall_tool_call_signal_sink()
    if tool_call_feedback.tracker is not None:
        # Clear the slice so wire_tool_call_feedback re-wires on the next
        # lifespan entry (its idempotency guard checks ``tracker``).
        app_state.swap_slice(tool_call_feedback.model_copy(update={"tracker": None}))
        logger.debug(
            API_APP_SHUTDOWN,
            service="tool_call_feedback",
            note="sink uninstalled",
        )

    hr_slice = app_state.slice(HrStateSlice)
    if hr_slice.eval_loop_cycle_scheduler is not None:
        await _try_stop(
            hr_slice.eval_loop_cycle_scheduler.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop eval-loop cycle scheduler",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="eval_loop_cycle_scheduler",
        )
    if hr_slice.eval_loop_coordinator is not None:
        # Clear coordinator + scheduler so wire_eval_loop re-wires on the next
        # lifespan entry (its idempotency guard checks ``eval_loop_coordinator``).
        app_state.wire(
            HrStateSlice,
            eval_loop_coordinator=None,
            eval_loop_cycle_scheduler=None,
        )

    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415

    budget_slice = app_state.slice(BudgetStateSlice)
    if budget_slice.quota_poller is not None:
        await _try_stop(
            budget_slice.quota_poller.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop quota poller",
            timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="quota_poller",
        )
        app_state.wire(BudgetStateSlice, quota_poller=None)

    si_service = meta_slice.self_improvement_service
    if si_service is not None:
        # ``close`` drains the appliers' GitHub HTTP clients, the rollback
        # executor's branch-revert client, and the analytics emitter's
        # periodic-flush task; without it those connection pools and the
        # flush task leak past the app lifecycle.
        await _try_stop(
            si_service.close(),
            API_APP_SHUTDOWN,
            "Failed to close self-improvement service",
            timeout=_DRAINING_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="self_improvement_service",
        )
        app_state.wire(MetaStateSlice, self_improvement_service=None)

    # Stop every cached rate-limit coordinator and clear the module-level
    # factory so background poll tasks and bus subscriptions cannot outlive the
    # app (matters for hot-reload / test teardown where ``create_app`` runs
    # multiple times in the same process).
    try:
        from synthorg.integrations.rate_limiting import (  # noqa: PLC0415
            shared_state as _rate_limit_shared_state,
        )

        # Bounded: a coordinator whose ``stop`` hangs (NATS drain, stalled
        # HTTP close) must not block teardown past the SIGKILL deadline.
        await asyncio.wait_for(
            _rate_limit_shared_state.set_coordinator_factory(None),
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_SHUTDOWN,
            phase="rate_limit_coordinator_stop",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    # Clear the process-global ambient strategic-context and active-principle
    # providers bound at startup. Both are module-level (visible across every
    # request coroutine and the render worker), so a stale snapshot would
    # outlive the app and leak into the next lifespan entry on hot-reload /
    # test teardown -- the same hazard the rate-limit factory clears above.
    from synthorg.engine.strategy.active_principle_provider import (  # noqa: PLC0415
        set_active_principle_provider,
    )
    from synthorg.engine.strategy.principle_override_provider import (  # noqa: PLC0415
        set_principle_override_provider,
    )
    from synthorg.engine.strategy.strategic_context_provider import (  # noqa: PLC0415
        set_strategic_context_provider,
    )

    set_strategic_context_provider(None)
    set_active_principle_provider(None)
    set_principle_override_provider(None)
    if tasks.auto_wired_dispatcher is not None:
        await _try_stop(
            tasks.auto_wired_dispatcher.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop auto-wired settings dispatcher",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="auto_wired_settings_dispatcher",
        )
        tasks.auto_wired_dispatcher = None
    # Stop the durable audit-chain writer BEFORE ``_safe_shutdown`` runs, because
    # that call disconnects persistence; the writer's drain flushes queued audit
    # entries through ``repo.append()``, which needs a live backend. Running it
    # afterwards would flush against a disconnected DB and drop the tail. The
    # sink stays attached as a logging handler (late shutdown lines still chain);
    # only its durable writer is stopped here.
    from synthorg.observability.audit_chain.sink import (  # noqa: PLC0415
        AuditChainSink,
    )
    from synthorg.observability.sinks import (  # noqa: PLC0415
        iter_logging_handlers,
    )

    for handler in iter_logging_handlers():
        if isinstance(handler, AuditChainSink):
            await _try_stop(
                handler.aclose_persistence(),
                API_APP_SHUTDOWN,
                "Failed to close audit-chain persistence",
                timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
                service="audit_chain_persistence",
            )
    await _safe_shutdown(
        task_engine,
        meeting_scheduler,
        backup_service,
        approval_timeout_scheduler,
        settings_dispatcher,
        bridge,
        message_bus,
        persistence,
        performance_tracker=app_state.slice(HrStateSlice).performance_tracker,
        distributed_task_queue=app_state.slice(
            RuntimeStateSlice
        ).distributed_task_queue,
        distributed_backend_services=app_state.slice(
            RuntimeStateSlice
        ).distributed_backend_services,
        # Drained inside _safe_shutdown after the message bus but before
        # persistence.disconnect, so a final delivery flush still reaches
        # the DB instead of failing against a disconnected backend.
        notification_dispatcher=app_state.slice(NotificationsStateSlice).dispatcher,
    )
    # Close the A2A outbound HTTP client if wired. Routed through ``_try_stop``
    # with a bounded timeout (like every other stop step) so a hung keep-alive
    # socket or stalled TLS shutdown cannot block teardown past the SIGKILL
    # deadline; a bare ``await aclose()`` would hang silently.
    a2a_client_obj = app_state.slice(A2aStateSlice).client
    if a2a_client_obj is not None and hasattr(a2a_client_obj, "aclose"):
        await _try_stop(
            a2a_client_obj.aclose(),
            API_APP_SHUTDOWN,
            "Failed to close A2A outbound HTTP client",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="a2a_client",
        )
    # Flush + stop the OTLP trace handler so the BatchSpanProcessor thread
    # drains queued spans and exits instead of leaking past process teardown.
    # Bounded like every other stop step so a stalled exporter cannot block
    # shutdown past the SIGKILL deadline.
    trace_handler = app_state.slice(ObservabilityStateSlice).trace_handler
    if trace_handler is not None:
        await _try_stop(
            trace_handler.shutdown(),
            API_APP_SHUTDOWN,
            "Failed to shut down OTLP trace handler",
            timeout=_SERVICE_STOP_SHUTDOWN_SECONDS,
            service="otlp_trace_handler",
        )
    # Flush and close buffering log handlers last, once every other
    # service has emitted its shutdown lines, so the OTLP exporter's
    # queued records and flusher thread are not lost on exit.
    from synthorg.observability.setup import teardown_logging  # noqa: PLC0415

    await teardown_logging()
