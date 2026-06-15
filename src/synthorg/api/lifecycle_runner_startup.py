# module-kind: orchestrator
"""The on-startup runner: wires persistence-bound services after connect.

``_run_startup`` is the body of the historic ``on_startup`` closure lifted to a
top-level function. Its former ``nonlocal`` janitor-task / dispatcher /
health-prober state now lives on the shared :class:`_LifecycleTasks` container
threaded in by the builder; the three task-done callbacks are passed as
parameters so the builder owns their domain-event routing.
"""

import asyncio
from collections.abc import Callable

from synthorg import __version__
from synthorg.api.bus_bridge import MessageBusBridge
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
from synthorg.api.lifecycle_runner_support import (
    _LifecycleTasks,
    _wire_approval_gate,
    _wire_workflow_observer,
)
from synthorg.api.state import AppState
from synthorg.api.webhook_cleanup import _webhook_receipt_cleanup_loop
from synthorg.approval.state import ApprovalStateSlice
from synthorg.backup.service import BackupService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.communication.state import CommunicationStateSlice
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.state import (
    HrStateSlice,
    agent_registry_of,
    training_service_of,
)
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRE_FAILED,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.observability.state import ObservabilityStateSlice
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.state import has_active_provider
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.state import SettingsStateSlice, config_resolver_of
from synthorg.tools.state import ToolsStateSlice, tool_invocation_tracker_of
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)


async def _run_startup(  # noqa: PLR0913
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
    should_auto_wire_settings: bool,
    effective_config: RootConfig | None,
    on_ticket_cleanup_done: Callable[[asyncio.Task[None]], None],
    on_audit_retention_done: Callable[[asyncio.Task[None]], None],
    on_webhook_cleanup_done: Callable[[asyncio.Task[None]], None],
) -> None:
    """Run the on-startup wiring once persistence is connected.

    Args:
        tasks: Shared mutable handles the shutdown runner later tears down.
        app_state: Application state container.
        persistence: Persistence backend (``None`` when unconfigured).
        message_bus: Internal message bus (``None`` when unconfigured).
        bridge: Message bus bridge to WebSocket channels.
        settings_dispatcher: Settings change dispatcher.
        task_engine: Centralized task state engine.
        meeting_scheduler: Meeting scheduler service.
        backup_service: Backup and restore service.
        approval_timeout_scheduler: Background approval timeout checker.
        should_auto_wire_settings: When ``True``, create ``SettingsService`` +
            dispatcher after persistence connects.
        effective_config: Root config needed for on-startup auto-wiring.
        on_ticket_cleanup_done: Done-callback for the ticket-cleanup loop.
        on_audit_retention_done: Done-callback for the audit-retention loop.
        on_webhook_cleanup_done: Done-callback for the webhook-cleanup loop.

    Raises:
        MemoryError: Re-raised unchanged from the training-service wire.
        RecursionError: Re-raised unchanged from the training-service wire.
        Exception: Re-raised after ``_safe_shutdown`` when the SettingsService
            or workflow-observer auto-wire fails, or when the approval-gate
            wire fails in provider-present mode.
    """
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

    # Install POSIX SIGTERM/SIGINT handlers.  Logs the incoming signal and
    # flags ``app_state.shutdown_requested`` so long-lived loops can exit early
    # instead of waiting for lifespan cancellation.  No-op on Windows.
    from synthorg.api.signals import (  # noqa: PLC0415
        install_shutdown_handlers,
    )

    install_shutdown_handlers(app_state)

    # Auto-wire the agent registry's identity-versioning service now that
    # persistence is connected.  Running this before ``_safe_startup`` would
    # access ``persistence.identity_versions`` on a disconnected backend, which
    # raises and drops the system into a no-versioning state (lost audit trail
    # on rollback/evolve).
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                phase="prometheus_collector_init",
                severity="non_fatal",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    # Wire distributed trace handler and bridge OTLP log / audit-chain export
    # outcomes to the Prometheus collector.  ``wire_observability_callbacks`` is
    # idempotent so it is safe to re-run across test-fixture startup cycles.
    try:
        from synthorg.observability.startup_wiring import (  # noqa: PLC0415
            wire_observability_callbacks,
        )

        wire_observability_callbacks(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            phase="observability_callback_wiring",
            severity="non_fatal",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    # Persistence-gated service auto-wires (oauth-state, training-plan,
    # workflow-rollback, workflow-version, agent-version). Each is best-effort +
    # idempotent; extracted to keep this hook readable.
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

            tasks.auto_wired_dispatcher = await auto_wire_settings(
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
            # On-startup auto-wire pulls operator settings (incl. secret-bearing
            # config). Avoid logger.exception here so traceback frame-locals
            # never serialize raw secrets to the log sink.
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
                performance_tracker=app_state.slice(HrStateSlice).performance_tracker,
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
    # register_observer() raise here still triggers _safe_shutdown instead of
    # leaving the app half-wired.
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
                performance_tracker=app_state.slice(HrStateSlice).performance_tracker,
                distributed_task_queue=app_state.slice(
                    RuntimeStateSlice
                ).distributed_task_queue,
                distributed_backend_services=app_state.slice(
                    RuntimeStateSlice
                ).distributed_backend_services,
            )
            raise

    # Single boot ApprovalGate: wired here (after persistence connects, before
    # the appended worker-execution-service install hook reads the
    # ``ApprovalStateSlice`` gate) so the engine parks and the /approvals
    # controller resumes on one gate. Non-fatal: a failure degrades to the
    # review-gate flow rather than aborting boot, matching the other
    # persistence-bound auto-wires.
    try:
        await _wire_approval_gate(persistence, app_state)
    except Exception as exc:
        reraise_critical(exc)
        # In provider-present mode the engine WILL run agents and park them; if
        # the shared gate is unset the runtime builds its own private gate from
        # _approval_store, splitting park and resume across instances so parked
        # runs can never be resumed via /approvals. A boot that "succeeds" into
        # that state is worse than a clear failure -- abort. Without a provider
        # no agent runs, so the review-gate degrade is acceptable and stays a
        # warning.
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="approval_gate",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        if has_active_provider(app_state):
            raise

    # When an external caller already supplied a ``TrainingService`` to
    # ``create_app()``, we skip the auto-wire below but the injected service
    # still owns a live ``MemoryBackend``. Pull it out and publish it on
    # ``app_state`` so the DELETE memory controller and MCP tool path see
    # ``has_memory_backend == True`` -- otherwise an injected-service deployment
    # would surface as 501 / unsupported even though a connected backend is
    # right there.
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
    # Needs agent_registry, tool_invocation_tracker, and performance_tracker
    # (all wired at construction time).  Uses InMemoryBackend for the memory
    # layer; production callers inject a real Mem0 backend via the
    # training_service param.
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
            from synthorg.memory.factory import (  # noqa: PLC0415
                build_in_memory_backend,
            )

            _perf = app_state.slice(HrStateSlice).performance_tracker
            if _perf is not None:
                _mem = build_in_memory_backend()
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
                    # ``delete_memory`` MCP tool can route through one connected
                    # backend instance per process.
                    if app_state.slice(MemoryStateSlice).backend is None:
                        app_state.wire(MemoryStateSlice, backend=_mem)
                except MemoryError, RecursionError:
                    await _mem.disconnect()
                    raise
                except Exception:
                    await _mem.disconnect()
                    raise
                tasks.training_memory_backend = _mem
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
    # Idempotent: a prior ticket-cleanup task from a previous startup may still
    # be alive when lifespan re-enters (e.g. shared-app test fixture).  Cancel
    # it before spawning a fresh one so tasks do not accumulate.  Any
    # non-cancellation exception from the prior task has already been logged by
    # ``on_ticket_cleanup_done``; it is discarded here because we are replacing
    # the task, not handling its outcome.
    if tasks.ticket_cleanup_task is not None and not tasks.ticket_cleanup_task.done():
        tasks.ticket_cleanup_task.cancel()
        try:
            await tasks.ticket_cleanup_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
    await _apply_bridge_config(app_state, effective_config)
    await _apply_security_timeout_interval(app_state, approval_timeout_scheduler)

    # Rebind the live ``MessageBusBridge`` to the now-wired resolver.
    # ``create_app`` captures the resolver eagerly when the bridge is
    # constructed; on the auto-wire path the resolver is not yet available at
    # that moment, so the bridge is built with ``None`` and would otherwise read
    # the registered defaults forever.
    if (
        bridge is not None
        and app_state.slice(SettingsStateSlice).config_resolver is not None
    ):
        bridge.set_config_resolver(config_resolver_of(app_state))

    tasks.ticket_cleanup_task = asyncio.create_task(
        _ticket_cleanup_loop(app_state),
        name="ws-ticket-cleanup",
    )
    tasks.ticket_cleanup_task.add_done_callback(on_ticket_cleanup_done)

    # CFG-1: audit retention purge loop (once every 24h).
    # Idempotent: cancel any prior retention task before spawning a fresh one so
    # tasks do not accumulate when lifespan re-enters.
    if tasks.audit_retention_task is not None and not tasks.audit_retention_task.done():
        tasks.audit_retention_task.cancel()
        try:
            await tasks.audit_retention_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
    tasks.audit_retention_task = asyncio.create_task(
        _audit_retention_loop(app_state),
        name="audit-retention",
    )
    tasks.audit_retention_task.add_done_callback(on_audit_retention_done)

    # Webhook-receipt sweep loop (once every 24h).  Idempotent: cancel any prior
    # sweep task before spawning a fresh one so tasks do not accumulate when
    # lifespan re-enters.
    if tasks.webhook_cleanup_task is not None and not tasks.webhook_cleanup_task.done():
        tasks.webhook_cleanup_task.cancel()
        try:
            await tasks.webhook_cleanup_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
    tasks.webhook_cleanup_task = asyncio.create_task(
        _webhook_receipt_cleanup_loop(app_state),
        name="webhook-receipt-cleanup",
    )
    tasks.webhook_cleanup_task.add_done_callback(on_webhook_cleanup_done)
    # Idempotent: stop any prior health prober instance before starting a new
    # one so probers do not accumulate when the shared app re-enters lifespan.
    if tasks.health_prober is not None:
        await _try_stop(
            tasks.health_prober.stop(),
            API_APP_STARTUP,
            "Failed to stop prior health prober before restart",
        )
        tasks.health_prober = None
    tasks.health_prober = await _maybe_start_health_prober(app_state)

    # Start integration background services (non-fatal).
    webhook_event_bridge = app_state.slice(IntegrationsStateSlice).webhook_event_bridge
    if webhook_event_bridge is not None:
        try:
            await webhook_event_bridge.start()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                phase="escalation_notify_subscriber_start",
                severity="non_fatal",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    # EventStreamHub inactivity-TTL janitor. Without this, an SSE client that
    # disconnects without unsubscribe (browser-tab kill, network partition)
    # leaks its queue + per-session dedup window for the lifetime of the
    # process.
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                phase="event_stream_hub_start",
                severity="non_fatal",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
