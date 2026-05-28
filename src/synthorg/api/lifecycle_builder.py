"""Startup/shutdown lifecycle builder for the Litestar application.

The two-phase wiring helpers live here so ``api/app.py`` stays a
thin entry point: construct-phase wires synchronous services
(registries, factories) while ``on_startup`` wires services that
require a connected persistence backend.
"""

import asyncio
from typing import TYPE_CHECKING, cast

from synthorg import __version__
from synthorg.a2a.state import A2aStateSlice
from synthorg.api.lifecycle import (
    _maybe_start_health_prober,
    _safe_shutdown,
    _safe_startup,
    _try_stop,
)
from synthorg.api.lifecycle_helpers.audit_retention import _audit_retention_loop
from synthorg.api.lifecycle_helpers.bootstrap import (
    _maybe_bootstrap_agents,
    _maybe_promote_first_owner,
)
from synthorg.api.lifecycle_helpers.config_apply import (
    _apply_bridge_config,
    _apply_security_timeout_interval,
)
from synthorg.api.lifecycle_helpers.persistence_autowire import (
    wire_persistence_services,
)
from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.api.lifecycle_helpers.ticket_cleanup import (
    _resolve_event_stream_janitor_settings,
    _ticket_cleanup_loop,
)
from synthorg.api.webhook_cleanup import _webhook_receipt_cleanup_loop
from synthorg.approval.state import ApprovalStateSlice
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.state import (
    HrStateSlice,
    agent_registry_of,
    training_service_of,
)
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.memory.state import MemoryStateSlice
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_SHUTDOWN,
    API_APP_SHUTDOWN_TIMEOUT,
    API_APP_STARTUP,
    API_AUDIT_RETENTION,
    API_SERVICE_AUTO_WIRE_FAILED,
    API_SERVICE_AUTO_WIRED,
    API_WS_TICKET_CLEANUP,
)
from synthorg.observability.events.persistence import (
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
)
from synthorg.observability.state import ObservabilityStateSlice
from synthorg.providers.state import has_active_provider
from synthorg.settings.dispatcher import SettingsChangeDispatcher  # noqa: TC001
from synthorg.settings.state import SettingsStateSlice, config_resolver_of
from synthorg.tools.state import ToolsStateSlice, tool_invocation_tracker_of
from synthorg.workers.state import RuntimeStateSlice

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime as _datetime

    from synthorg.api.bus_bridge import MessageBusBridge
    from synthorg.api.state import AppState
    from synthorg.backup.service import BackupService
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.meeting.scheduler import MeetingScheduler
    from synthorg.config.schema import RootConfig
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.providers.health_prober import ProviderHealthProber
    from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler

    _ = _datetime  # keep import consistent with original module

logger = get_logger(__name__)


# Per-task shutdown budgets for the three janitor loops launched by the
# lifecycle builder. These are passive wake-poll-sleep loops so 2.0s
# matches the budget already used for the meeting scheduler / settings
# dispatcher / bus-bridge in ``api/lifecycle.py`` (see the worst-case
# math comment near ``_TASK_ENGINE_SHUTDOWN_SECONDS``). Wrapping the
# cancel-and-await with ``asyncio.wait_for`` keeps shutdown bounded
# even when a task body shields ``CancelledError`` (third-party
# callees, hung I/O); the orchestrator's SIGKILL deadline must not
# slip past ``graceful_shutdown`` (75s in api/server.py).
_TICKET_CLEANUP_SHUTDOWN_SECONDS: float = 2.0
_AUDIT_RETENTION_SHUTDOWN_SECONDS: float = 2.0
_WEBHOOK_CLEANUP_SHUTDOWN_SECONDS: float = 2.0


async def _cancel_with_timeout(
    task: asyncio.Task[None],
    *,
    service: str,
    timeout: float,  # noqa: ASYNC109 -- per-task shutdown budget
) -> None:
    """Cancel *task* and await completion with a hard timeout.

    The three janitor loops the lifecycle builder owns
    (``_ticket_cleanup_task``, ``_audit_retention_task``,
    ``_webhook_cleanup_task``) are wake-poll-sleep loops with no
    in-flight work, so a body that shields ``CancelledError`` is the
    only realistic way to hang here. Bound the wait to *timeout* and
    log at ERROR via ``API_APP_SHUTDOWN_TIMEOUT`` when the budget
    elapses; downstream service-teardown still runs.

    ``CancelledError`` is the normal completion path and is suppressed.
    ``MemoryError`` / ``RecursionError`` are re-raised because they
    must surface (matches the ``_try_stop`` discipline in
    ``api/lifecycle.py``).
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
    except Exception as exc:
        # A janitor task that fails with a non-timeout exception
        # (third-party callee crashing inside its except clause, hung
        # I/O surfacing OSError, aiosqlite raising during a partial
        # write) must not be silently swallowed -- the previous
        # implementation returned ``None`` from this branch and the
        # service-teardown sequence continued thinking the cancel
        # succeeded. Log at ERROR via the shutdown event so the
        # operator sees the underlying cause, then continue with
        # downstream services (the helper's contract is "never block
        # the whole shutdown window").
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
    drives `max_subworkflow_depth`. Falls back to the seed default (with
    INFO log) when no resolver is wired, so executions still advance.
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
                "config_resolver not wired; registering observer "
                "with the EngineBridgeConfig seed default for "
                "max_subworkflow_depth"
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

    One gate, shared by both governance sides: the engine parks blocked
    contexts (the gate is injected into ``AgentEngine`` by
    ``runtime_builder``) and the ``/approvals`` controller resumes them
    (read via the ``ApprovalStateSlice`` gate). Park and resume must
    operate on the same gate over the same ``ParkedContextRepository`` or
    a parked context can never be found again on the decision side.

    Idempotent: a re-entered lifespan (shared-app test fixtures) skips
    when a gate is already wired. When persistence is absent or not
    connected the gate is still constructed (with no parked repo) so the
    single-gate invariant and the review-gate flow hold; resume of a
    persisted context is simply unavailable without a backend.
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
    # configured approval-interrupt timeout must be threaded in here
    # explicitly or any non-default setting is silently ignored once
    # the shared gate is in use. When the resolver is not yet wired
    # (early boot / minimal test states) fall back to the
    # EngineBridgeConfig seed default rather than failing gate wiring.
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


def _build_lifecycle(  # noqa: PLR0913
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
    app_state: AppState,
    *,
    should_auto_wire_settings: bool = False,
    effective_config: RootConfig | None = None,
) -> tuple[
    Sequence[Callable[[], Awaitable[None]]],
    Sequence[Callable[[], Awaitable[None]]],
]:
    """Build startup and shutdown hooks.

    Args:
        persistence: Persistence backend (``None`` when unconfigured).
        message_bus: Internal message bus (``None`` when unconfigured).
        bridge: Message bus bridge to WebSocket channels.
        settings_dispatcher: Settings change dispatcher.
        task_engine: Centralized task state engine.
        meeting_scheduler: Meeting scheduler service.
        backup_service: Backup and restore service.
        approval_timeout_scheduler: Background approval timeout checker.
        app_state: Application state container.
        should_auto_wire_settings: When ``True``, on-startup
            auto-wiring creates ``SettingsService`` + dispatcher after
            persistence connects.
        effective_config: Root config needed for on-startup auto-wiring.

    Returns:
        A tuple of (on_startup, on_shutdown) callback lists.
    """
    _ticket_cleanup_task: asyncio.Task[None] | None = None
    _audit_retention_task: asyncio.Task[None] | None = None
    _webhook_cleanup_task: asyncio.Task[None] | None = None
    _auto_wired_dispatcher: SettingsChangeDispatcher | None = None
    _health_prober: ProviderHealthProber | None = None
    _training_memory_backend: object | None = None

    def _make_cleanup_done_callback(
        event: str,
        message: str,
    ) -> Callable[[asyncio.Task[None]], None]:
        """Build a task-done callback that logs under a domain event.

        The ticket-cleanup and audit-retention loops both want the same
        "log if it died unexpectedly" semantics but need different
        observability event names so a compliance-affecting retention
        outage is not mis-routed to the WebSocket cleanup channel.
        """

        def _callback(task: asyncio.Task[None]) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                log_exception_redacted(logger, event, exc, note=message)

        return _callback

    _on_ticket_cleanup_done = _make_cleanup_done_callback(
        API_WS_TICKET_CLEANUP,
        "Ticket cleanup task died unexpectedly",
    )
    _on_audit_retention_done = _make_cleanup_done_callback(
        API_AUDIT_RETENTION,
        "Audit retention task died unexpectedly",
    )
    _on_webhook_cleanup_done = _make_cleanup_done_callback(
        PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
        "Webhook receipt cleanup task died unexpectedly",
    )

    async def on_startup() -> None:
        nonlocal _ticket_cleanup_task, _audit_retention_task
        nonlocal _webhook_cleanup_task
        nonlocal _auto_wired_dispatcher
        nonlocal _health_prober, _training_memory_backend
        logger.info(API_APP_STARTUP, version=__version__)
        await _safe_startup(
            persistence,
            message_bus,
            bridge,
            settings_dispatcher,
            task_engine,
            meeting_scheduler,
            backup_service,
            approval_timeout_scheduler,
            app_state,
        )

        # Install POSIX SIGTERM/SIGINT handlers.  Logs the incoming
        # signal and flags ``app_state.shutdown_requested`` so
        # long-lived loops can exit early instead of waiting for
        # lifespan cancellation.  No-op on Windows / non-POSIX loops.
        from synthorg.api.signals import (  # noqa: PLC0415
            install_shutdown_handlers,
        )

        install_shutdown_handlers(app_state)

        # Auto-wire the agent registry's identity-versioning service now
        # that persistence is connected.  Running this before
        # ``_safe_startup`` would access ``persistence.identity_versions``
        # on a disconnected backend, which raises and drops the system
        # into a no-versioning state (lost audit trail on rollback/evolve).
        if (
            app_state.slice(HrStateSlice).agent_registry is not None
            and persistence is not None
            and getattr(persistence, "is_connected", False)
            and not agent_registry_of(app_state).has_versioning
        ):
            try:
                from synthorg.versioning import VersioningService  # noqa: PLC0415

                agent_registry_of(app_state).bind_versioning(
                    VersioningService(persistence.identity_versions),
                )
                logger.info(
                    API_SERVICE_AUTO_WIRED,
                    service="agent_registry_versioning",
                )
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_SERVICE_AUTO_WIRE_FAILED,
                    service="agent_registry_versioning",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        # Wire Prometheus collector (no dependencies, runs in-process).
        # Non-fatal: /metrics degrades to 503 if this fails.
        if app_state.slice(ObservabilityStateSlice).prometheus_collector is None:
            try:
                from synthorg.observability.prometheus_collector import (  # noqa: PLC0415
                    PrometheusCollector,
                )

                collector = PrometheusCollector()
                app_state.swap_slice(
                    app_state.slice(ObservabilityStateSlice).model_copy(
                        update={"prometheus_collector": collector}
                    )
                )
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_APP_STARTUP,
                    phase="prometheus_collector_init",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        # Wire distributed trace handler and bridge OTLP log /
        # audit-chain export outcomes to the Prometheus collector.
        # ``wire_observability_callbacks`` is idempotent so it is
        # safe to re-run across test-fixture startup cycles.
        try:
            from synthorg.observability.startup_wiring import (  # noqa: PLC0415
                wire_observability_callbacks,
            )

            wire_observability_callbacks(app_state)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                phase="observability_callback_wiring",
                severity="non_fatal",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

        # Persistence-gated service auto-wires (oauth-state, training-plan,
        # workflow-rollback, workflow-version, agent-version). Each is
        # best-effort + idempotent; extracted to keep this hook readable.
        await wire_persistence_services(app_state, persistence)

        # On-startup auto-wire: SettingsService (needs connected persistence)
        if (
            should_auto_wire_settings
            and persistence is not None
            and effective_config is not None
            and app_state.slice(SettingsStateSlice).settings_service is None
        ):
            try:
                from synthorg.api.auto_wire import auto_wire_settings  # noqa: PLC0415

                _auto_wired_dispatcher = await auto_wire_settings(
                    persistence,
                    message_bus,
                    effective_config,
                    app_state,
                    backup_service,
                    _build_settings_dispatcher,
                    approval_timeout_scheduler,
                )
            except Exception as exc:
                reraise_critical(exc)
                # On-startup auto-wire pulls operator settings (incl.
                # secret-bearing config). Avoid logger.exception here
                # so traceback frame-locals never serialize raw
                # secrets to the log sink.
                log_exception_redacted(
                    logger, API_APP_STARTUP, exc, detail="settings_auto_wire_failed"
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
                    performance_tracker=app_state.slice(
                        HrStateSlice
                    ).performance_tracker,
                    distributed_task_queue=app_state.slice(
                        RuntimeStateSlice
                    ).distributed_task_queue,
                    distributed_backend_services=app_state.slice(
                        RuntimeStateSlice
                    ).distributed_backend_services,
                )
                raise
        # AFTER SettingsService auto-wire; resolver drives max_subworkflow_depth.
        # Mirror the auto_wire_settings failure path so a resolver or
        # register_observer() raise here still triggers _safe_shutdown
        # instead of leaving the app half-wired.
        if persistence is not None:
            try:
                await _wire_workflow_observer(task_engine, persistence, app_state)
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    API_APP_STARTUP,
                    exc,
                    detail="workflow_observer_auto_wire_failed",
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
                    performance_tracker=app_state.slice(
                        HrStateSlice
                    ).performance_tracker,
                    distributed_task_queue=app_state.slice(
                        RuntimeStateSlice
                    ).distributed_task_queue,
                    distributed_backend_services=app_state.slice(
                        RuntimeStateSlice
                    ).distributed_backend_services,
                )
                raise

        # Single boot ApprovalGate: wired here (after persistence
        # connects, before the appended worker-execution-service install
        # hook reads the ``ApprovalStateSlice`` gate) so the engine parks
        # and the /approvals controller resumes on one gate. Non-fatal:
        # a failure degrades to the review-gate flow rather than aborting
        # boot, matching the other persistence-bound auto-wires.
        try:
            await _wire_approval_gate(persistence, app_state)
        except Exception as exc:
            reraise_critical(exc)
            # In provider-present mode the engine WILL run agents and
            # park them; if the shared gate is unset the runtime builds
            # its own private gate from _approval_store, splitting park
            # and resume across instances so parked runs can never be
            # resumed via /approvals. A boot that "succeeds" into that
            # state is worse than a clear failure -- abort. Without a
            # provider no agent runs, so the review-gate degrade is
            # acceptable and stays a warning.
            logger.warning(
                API_SERVICE_AUTO_WIRE_FAILED,
                service="approval_gate",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if has_active_provider(app_state):
                raise

        # When an external caller already supplied a
        # ``TrainingService`` to ``create_app()``, we skip the
        # auto-wire below but the injected service still owns a live
        # ``MemoryBackend``. Pull it out and publish it on
        # ``app_state`` so the DELETE memory controller and MCP tool
        # path see ``has_memory_backend == True`` -- otherwise an
        # injected-service deployment would surface as 501 / unsupported
        # even though a connected backend is right there.
        hr_slice = app_state.slice(HrStateSlice)
        if (
            hr_slice.training_service is not None
            and app_state.slice(MemoryStateSlice).backend is None
        ):
            injected_backend = getattr(
                training_service_of(app_state),
                "_memory_backend",
                None,
            )
            if injected_backend is not None:
                app_state.wire(MemoryStateSlice, backend=injected_backend)

        # On-startup auto-wire: TrainingService.
        # Needs agent_registry, tool_invocation_tracker, and
        # performance_tracker (all wired at construction time).  Uses
        # InMemoryBackend for the memory layer; production callers
        # inject a real Mem0 backend via the training_service param.
        if (
            app_state.slice(HrStateSlice).training_service is None
            and effective_config is not None
            and effective_config.training.enabled
            and app_state.slice(HrStateSlice).agent_registry is not None
            and app_state.slice(ToolsStateSlice).invocation_tracker is not None
        ):
            try:
                from synthorg.hr.training.factory import (  # noqa: PLC0415
                    build_training_service,
                )
                from synthorg.memory.backends.inmemory import (  # noqa: PLC0415
                    InMemoryBackend,
                )

                _perf = app_state.slice(HrStateSlice).performance_tracker
                if _perf is not None:
                    _mem = InMemoryBackend()
                    await _mem.connect()
                    from synthorg._core.features import (  # noqa: PLC0415
                        require_service,
                    )

                    try:
                        _ts = build_training_service(
                            config=effective_config.training,
                            memory_backend=_mem,
                            tracker=_perf,
                            registry=agent_registry_of(app_state),
                            approval_store=require_service(
                                app_state.slice(ApprovalStateSlice).store,
                                "Approval Store",
                            ),
                            tool_tracker=tool_invocation_tracker_of(app_state),
                        )
                        app_state.wire(HrStateSlice, training_service=_ts)
                        # Expose the same backend to admin paths so
                        # ``DELETE /agents/{id}/memories/{id}`` and the
                        # ``delete_memory`` MCP tool can route through
                        # one connected backend instance per process.
                        if app_state.slice(MemoryStateSlice).backend is None:
                            app_state.wire(MemoryStateSlice, backend=_mem)
                    except MemoryError, RecursionError:
                        await _mem.disconnect()
                        raise
                    except Exception:
                        await _mem.disconnect()
                        raise
                    _training_memory_backend = _mem
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_APP_STARTUP,
                    phase="training_service_auto_wire",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        await _maybe_bootstrap_agents(app_state)
        await _maybe_promote_first_owner(app_state)
        # Idempotent: a prior ticket-cleanup task from a previous
        # startup may still be alive when lifespan re-enters (e.g.
        # shared-app test fixture).  Cancel it before spawning a
        # fresh one so tasks do not accumulate.  Any non-cancellation
        # exception from the prior task has already been logged by
        # ``_on_ticket_cleanup_done``; it is discarded here because we
        # are replacing the task, not handling its outcome.
        if _ticket_cleanup_task is not None and not _ticket_cleanup_task.done():
            _ticket_cleanup_task.cancel()
            try:
                await _ticket_cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                reraise_critical(exc)
        await _apply_bridge_config(app_state, effective_config)
        await _apply_security_timeout_interval(app_state, approval_timeout_scheduler)

        # Rebind the live ``MessageBusBridge`` to the now-wired
        # resolver. ``create_app`` captures the resolver eagerly when
        # the bridge is constructed; on the auto-wire path the
        # resolver is not yet available at that moment, so the bridge
        # is built with ``None`` and would otherwise read the
        # registered defaults forever.
        if (
            bridge is not None
            and app_state.slice(SettingsStateSlice).config_resolver is not None
        ):
            bridge.set_config_resolver(config_resolver_of(app_state))

        _ticket_cleanup_task = asyncio.create_task(
            _ticket_cleanup_loop(app_state),
            name="ws-ticket-cleanup",
        )
        _ticket_cleanup_task.add_done_callback(_on_ticket_cleanup_done)

        # CFG-1: audit retention purge loop (once every 24h).
        # Idempotent: cancel any prior retention task before spawning a
        # fresh one so tasks do not accumulate when lifespan re-enters.
        if _audit_retention_task is not None and not _audit_retention_task.done():
            _audit_retention_task.cancel()
            try:
                await _audit_retention_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                reraise_critical(exc)
        _audit_retention_task = asyncio.create_task(
            _audit_retention_loop(app_state),
            name="audit-retention",
        )
        _audit_retention_task.add_done_callback(_on_audit_retention_done)

        # Webhook-receipt sweep loop (once every 24h).  Idempotent:
        # cancel any prior sweep task before spawning a fresh one so
        # tasks do not accumulate when lifespan re-enters.
        if _webhook_cleanup_task is not None and not _webhook_cleanup_task.done():
            _webhook_cleanup_task.cancel()
            try:
                await _webhook_cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                reraise_critical(exc)
        _webhook_cleanup_task = asyncio.create_task(
            _webhook_receipt_cleanup_loop(app_state),
            name="webhook-receipt-cleanup",
        )
        _webhook_cleanup_task.add_done_callback(_on_webhook_cleanup_done)
        # Idempotent: stop any prior health prober instance before
        # starting a new one so probers do not accumulate when the
        # shared app re-enters lifespan.
        if _health_prober is not None:
            await _try_stop(
                _health_prober.stop(),
                API_APP_STARTUP,
                "Failed to stop prior health prober before restart",
            )
            _health_prober = None
        _health_prober = await _maybe_start_health_prober(app_state)

        # Start integration background services (non-fatal).
        webhook_event_bridge = app_state.slice(
            IntegrationsStateSlice
        ).webhook_event_bridge
        if webhook_event_bridge is not None:
            try:
                await webhook_event_bridge.start()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_APP_STARTUP,
                    phase="webhook_event_bridge_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        integrations = app_state.slice(IntegrationsStateSlice)
        communication = app_state.slice(CommunicationStateSlice)
        if integrations.health_prober_service is not None:
            try:
                await integrations.health_prober_service.start()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_APP_STARTUP,
                    phase="health_prober_service_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        if integrations.oauth_token_manager is not None:
            try:
                await integrations.oauth_token_manager.start()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_APP_STARTUP,
                    phase="oauth_token_manager_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        if communication.escalation_sweeper is not None:
            try:
                await communication.escalation_sweeper.start()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_APP_STARTUP,
                    phase="escalation_sweeper_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        if communication.escalation_notify_subscriber is not None:
            try:
                await communication.escalation_notify_subscriber.start()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_APP_STARTUP,
                    phase="escalation_notify_subscriber_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        # EventStreamHub inactivity-TTL janitor. Without this, an SSE
        # client that disconnects without unsubscribe (browser-tab kill,
        # network partition) leaks its queue + per-session dedup window
        # for the lifetime of the process.
        if communication.event_stream_hub is not None:
            try:
                (
                    idle_ttl,
                    janitor_interval,
                ) = await _resolve_event_stream_janitor_settings(app_state)
                await communication.event_stream_hub.start(
                    idle_ttl_seconds=idle_ttl,
                    janitor_interval_seconds=janitor_interval,
                )
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_APP_STARTUP,
                    phase="event_stream_hub_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    async def on_shutdown() -> None:
        nonlocal _ticket_cleanup_task, _audit_retention_task
        nonlocal _webhook_cleanup_task
        nonlocal _auto_wired_dispatcher
        nonlocal _health_prober, _training_memory_backend
        # Emit the shutdown event before any teardown step so the
        # gate-crossing is observable even if a downstream stop hangs
        # or raises. Mirrors the ``on_startup`` emission at the top of
        # that function.
        logger.info(API_APP_SHUTDOWN, version=__version__)
        # Drain in-flight parked-context resumes (background tasks
        # spawned off the /approvals path) before teardown so an
        # approved resume is not silently dropped mid-flight. Read the
        # runtime slice field directly; only the agent-runtime service
        # exposes ``drain_resume_tasks`` (the no-provider backstop does
        # not), so the ``getattr`` guard stays.
        _wes = app_state.slice(RuntimeStateSlice).worker_execution_service
        _drain_resumes = getattr(_wes, "drain_resume_tasks", None)
        if callable(_drain_resumes):
            await _try_stop(
                cast("Awaitable[None]", _drain_resumes()),
                API_APP_SHUTDOWN,
                "Failed to drain in-flight parked-context resumes",
            )
        # Disconnect training memory backend if auto-wired.
        if _training_memory_backend is not None:
            # If this backend was published to the memory slice at
            # startup, clear the field before disconnecting so a
            # subsequent re-entry of the lifespan can wire a fresh
            # connected backend without a stale handle lingering on the
            # slice.
            shared = app_state.slice(MemoryStateSlice).backend
            if shared is _training_memory_backend:
                app_state.swap_slice(
                    app_state.slice(MemoryStateSlice).model_copy(
                        update={"backend": None}
                    )
                )
            disconnect = getattr(_training_memory_backend, "disconnect", None)
            if callable(disconnect):
                # getattr + callable narrow statically only to ``object``
                # and "something callable", so the return type isn't
                # inferable.  Backends that expose a ``disconnect`` method
                # always return ``Awaitable[None]`` by contract
                # (see ``MemoryBackend.disconnect`` in training/memory).
                await _try_stop(
                    cast("Awaitable[None]", disconnect()),
                    API_APP_SHUTDOWN,
                    "Failed to disconnect training memory backend",
                )
            _training_memory_backend = None
        if _ticket_cleanup_task is not None:
            await _cancel_with_timeout(
                _ticket_cleanup_task,
                service="ticket_cleanup",
                timeout=_TICKET_CLEANUP_SHUTDOWN_SECONDS,
            )
            _ticket_cleanup_task = None
        if _audit_retention_task is not None:
            await _cancel_with_timeout(
                _audit_retention_task,
                service="audit_retention",
                timeout=_AUDIT_RETENTION_SHUTDOWN_SECONDS,
            )
            _audit_retention_task = None
        if _webhook_cleanup_task is not None:
            await _cancel_with_timeout(
                _webhook_cleanup_task,
                service="webhook_cleanup",
                timeout=_WEBHOOK_CLEANUP_SHUTDOWN_SECONDS,
            )
            _webhook_cleanup_task = None
        communication = app_state.slice(CommunicationStateSlice)
        integrations = app_state.slice(IntegrationsStateSlice)
        if communication.event_stream_hub is not None:
            await _try_stop(
                communication.event_stream_hub.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop event stream hub",
            )
        if _health_prober is not None:
            await _try_stop(
                _health_prober.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop health prober",
            )
            _health_prober = None
        # Stop integration background services (reverse start order).
        if communication.escalation_notify_subscriber is not None:
            await _try_stop(
                communication.escalation_notify_subscriber.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop escalation notify subscriber",
            )
        if communication.escalation_sweeper is not None:
            await _try_stop(
                communication.escalation_sweeper.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop escalation sweeper",
            )
        # Cancel any unresolved pending futures so coroutines awaiting
        # operator decisions get a clean CancelledError (instead of
        # hanging past shutdown) and the registry map is emptied.
        if communication.escalation_registry is not None:
            await _try_stop(
                communication.escalation_registry.close(),
                API_APP_SHUTDOWN,
                "Failed to close escalation pending-futures registry",
            )
        if integrations.oauth_token_manager is not None:
            await _try_stop(
                integrations.oauth_token_manager.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop OAuth token manager",
            )
        if integrations.health_prober_service is not None:
            await _try_stop(
                integrations.health_prober_service.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop integration health prober",
            )
        if integrations.webhook_event_bridge is not None:
            await _try_stop(
                integrations.webhook_event_bridge.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop webhook event bridge",
            )
        if integrations.tunnel_provider is not None:
            await _try_stop(
                integrations.tunnel_provider.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop tunnel provider",
            )
        # Stop every cached rate-limit coordinator and clear the
        # module-level factory so background poll tasks and bus
        # subscriptions cannot outlive the app (matters for
        # hot-reload / test teardown where ``create_app`` runs
        # multiple times in the same process).
        try:
            from synthorg.integrations.rate_limiting import (  # noqa: PLC0415
                shared_state as _rate_limit_shared_state,
            )

            await _rate_limit_shared_state.set_coordinator_factory(None)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_APP_SHUTDOWN,
                phase="rate_limit_coordinator_stop",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        if _auto_wired_dispatcher is not None:
            await _try_stop(
                _auto_wired_dispatcher.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop auto-wired settings dispatcher",
            )
            _auto_wired_dispatcher = None
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
        )
        notification_dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
        if notification_dispatcher is not None:
            await _try_stop(
                notification_dispatcher.aclose(),
                API_APP_SHUTDOWN,
                "Failed to stop notification dispatcher",
            )
        # Close A2A outbound HTTP client if wired.
        try:
            a2a_client_obj = app_state.slice(A2aStateSlice).client
            if a2a_client_obj is not None and hasattr(a2a_client_obj, "aclose"):
                await a2a_client_obj.aclose()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                API_APP_SHUTDOWN,
                phase="a2a_client_close",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return [on_startup], [on_shutdown]
