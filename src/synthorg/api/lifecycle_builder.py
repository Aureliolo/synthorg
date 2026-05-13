"""Startup/shutdown lifecycle builder for the Litestar application.

Contains the two-phase (construct + on_startup) wiring helpers that
were previously inlined in ``api/app.py``.
"""

import asyncio
from typing import TYPE_CHECKING, cast

from synthorg import __version__
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
from synthorg.api.lifecycle_helpers.settings_dispatcher import (
    _build_settings_dispatcher,
)
from synthorg.api.lifecycle_helpers.ticket_cleanup import (
    _resolve_event_stream_janitor_settings,
    _ticket_cleanup_loop,
)
from synthorg.api.webhook_cleanup import _webhook_receipt_cleanup_loop
from synthorg.observability import get_logger, safe_error_description
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
from synthorg.settings.dispatcher import SettingsChangeDispatcher  # noqa: TC001

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
        logger.error(
            API_APP_SHUTDOWN_TIMEOUT,
            service=service,
            timeout_seconds=timeout,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
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
        logger.error(
            API_APP_SHUTDOWN,
            service=service,
            phase="task_cancellation",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            context=f"{service} task crashed during cancellation",
        )


def _build_lifecycle(  # noqa: PLR0913, PLR0915, C901
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
                logger.error(
                    event,
                    note=message,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

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

    async def on_startup() -> None:  # noqa: C901, PLR0912, PLR0915
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
            app_state.has_agent_registry
            and persistence is not None
            and getattr(persistence, "is_connected", False)
            and not app_state.agent_registry.has_versioning
        ):
            try:
                from synthorg.versioning import VersioningService  # noqa: PLC0415

                app_state.agent_registry.bind_versioning(
                    VersioningService(persistence.identity_versions),
                )
                logger.info(
                    API_SERVICE_AUTO_WIRED,
                    service="agent_registry_versioning",
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_SERVICE_AUTO_WIRE_FAILED,
                    service="agent_registry_versioning",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        # Wire Prometheus collector (no dependencies, runs in-process).
        # Non-fatal: /metrics degrades to 503 if this fails.
        if not app_state.has_prometheus_collector:
            try:
                from synthorg.observability.prometheus_collector import (  # noqa: PLC0415
                    PrometheusCollector,
                )

                app_state.set_prometheus_collector(PrometheusCollector())
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
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
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                API_APP_STARTUP,
                phase="observability_callback_wiring",
                severity="non_fatal",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

        # Wire ``OAuthStateService`` once persistence + the
        # ``oauth_states`` repository are available.  Owns the only
        # durable write for OAuth-flow initiation so the
        # ``SECURITY_OAUTH_STATE_PERSISTED`` event fires alongside
        # every save.
        if (
            persistence is not None
            and getattr(persistence, "is_connected", False)
            and not app_state.has_oauth_state_service
            and hasattr(persistence, "oauth_states")
        ):
            try:
                from synthorg.integrations.oauth.state_service import (  # noqa: PLC0415
                    OAuthStateService,
                )

                app_state.set_oauth_state_service(
                    OAuthStateService(repo=persistence.oauth_states),
                )
                logger.info(
                    API_SERVICE_AUTO_WIRED,
                    service="oauth_state_service",
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_SERVICE_AUTO_WIRE_FAILED,
                    service="oauth_state_service",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        # Wire ``TrainingPlanService`` once persistence + the
        # ``training_plans`` / ``training_results`` repositories are
        # available.  Centralises every plan-CRUD write the
        # controller previously made directly so audit logging
        # cannot regress when a new write path is added.
        if (
            persistence is not None
            and getattr(persistence, "is_connected", False)
            and not app_state.has_training_plan_service
            and hasattr(persistence, "training_plans")
            and hasattr(persistence, "training_results")
        ):
            try:
                from synthorg.hr.training.plan_service import (  # noqa: PLC0415
                    TrainingPlanService,
                )

                app_state.set_training_plan_service(
                    TrainingPlanService(
                        plan_repo=persistence.training_plans,
                        result_repo=persistence.training_results,
                    ),
                )
                logger.info(
                    API_SERVICE_AUTO_WIRED,
                    service="training_plan_service",
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_SERVICE_AUTO_WIRE_FAILED,
                    service="training_plan_service",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        # Wire ``WorkflowRollbackService`` once persistence + the
        # ``workflow_definitions`` / ``versions`` repositories are
        # available.  Centralises the live save + post-rollback
        # snapshot writes the controller previously made directly so
        # audit logging cannot regress when a new write path lands in
        # the rollback contract.
        if (
            persistence is not None
            and getattr(persistence, "is_connected", False)
            and not app_state.has_workflow_rollback_service
            and hasattr(persistence, "workflow_definitions")
            and hasattr(persistence, "workflow_versions")
        ):
            try:
                from synthorg.api.services.workflow_rollback_service import (  # noqa: PLC0415
                    WorkflowRollbackService,
                )

                app_state.set_workflow_rollback_service(
                    WorkflowRollbackService(
                        definition_repo=persistence.workflow_definitions,
                        version_repo=persistence.workflow_versions,
                    ),
                )
                logger.info(
                    API_SERVICE_AUTO_WIRED,
                    service="workflow_rollback_service",
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_SERVICE_AUTO_WIRE_FAILED,
                    service="workflow_rollback_service",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        # Wire ``WorkflowVersionService`` and ``AgentVersionService`` so
        # the version-history controllers (workflow_versions.py and
        # agent_identity_versions.py) can read snapshots through the
        # service facade rather than reaching into
        # ``app_state.persistence.*_versions`` directly. Mirrors the
        # rollback wiring above and gates on the same persistence
        # readiness pre-conditions so a missing repository never
        # surfaces as a 500 when the controller pulls a service.
        if (
            persistence is not None
            and getattr(persistence, "is_connected", False)
            and not app_state.has_workflow_version_service
            and hasattr(persistence, "workflow_versions")
        ):
            try:
                from synthorg.engine.workflow.version_service import (  # noqa: PLC0415
                    WorkflowVersionService,
                )

                app_state.set_workflow_version_service(
                    WorkflowVersionService(
                        version_repo=persistence.workflow_versions,
                    ),
                )
                logger.info(
                    API_SERVICE_AUTO_WIRED,
                    service="workflow_version_service",
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_SERVICE_AUTO_WIRE_FAILED,
                    service="workflow_version_service",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        if (
            persistence is not None
            and getattr(persistence, "is_connected", False)
            and not app_state.has_agent_version_service
            and hasattr(persistence, "identity_versions")
        ):
            try:
                from synthorg.hr.identity.version_service import (  # noqa: PLC0415
                    AgentVersionService,
                )

                app_state.set_agent_version_service(
                    AgentVersionService(
                        version_repo=persistence.identity_versions,
                    ),
                )
                logger.info(
                    API_SERVICE_AUTO_WIRED,
                    service="agent_version_service",
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_SERVICE_AUTO_WIRE_FAILED,
                    service="agent_version_service",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        # On-startup auto-wire: SettingsService (needs connected persistence)
        if (
            should_auto_wire_settings
            and persistence is not None
            and effective_config is not None
            and not app_state.has_settings_service
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # On-startup auto-wire pulls operator settings (incl.
                # secret-bearing config). Avoid logger.exception here
                # so traceback frame-locals never serialize raw
                # secrets to the log sink.
                logger.error(
                    API_APP_STARTUP,
                    detail="settings_auto_wire_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
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
                    performance_tracker=app_state._performance_tracker,  # noqa: SLF001
                    distributed_task_queue=app_state.distributed_task_queue,
                )
                raise
        # Wire workflow execution observer (needs connected persistence
        # AND config_resolver). Registered AFTER the SettingsService
        # auto-wire above so config_resolver is in place and the
        # observer pulls ``max_subworkflow_depth`` from
        # ``EngineBridgeConfig`` instead of being pinned to the seed
        # default. ``register_observer`` is append-only and the
        # idempotency guard makes this block re-entrant against the
        # shared-app test fixture's startup cycles.
        if (
            task_engine is not None
            and persistence is not None
            and hasattr(persistence, "workflow_definitions")
            and hasattr(persistence, "workflow_executions")
        ):
            from synthorg.engine.workflow.execution_observer import (  # noqa: PLC0415
                WorkflowExecutionObserver,
            )

            _already_registered = any(
                isinstance(o, WorkflowExecutionObserver)
                for o in getattr(task_engine, "_observers", ())
            )
            if not _already_registered:
                if app_state.has_config_resolver:
                    engine_bridge = (
                        await app_state.config_resolver.get_engine_bridge_config()
                    )
                    max_depth = engine_bridge.max_subworkflow_depth
                else:
                    # No resolver: fall back to the EngineBridgeConfig
                    # seed default so the observer registers and
                    # workflow executions advance, instead of being
                    # silently dropped. Logged at INFO so operators
                    # know they will not pick up DB / env overrides
                    # for max_subworkflow_depth until a resolver is
                    # wired.
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
                _wf_observer = WorkflowExecutionObserver(
                    definition_repo=persistence.workflow_definitions,
                    execution_repo=persistence.workflow_executions,
                    task_engine=task_engine,
                    max_subworkflow_depth=max_depth,
                )
                task_engine.register_observer(_wf_observer)

        # When an external caller already supplied a
        # ``TrainingService`` to ``create_app()``, we skip the
        # auto-wire below but the injected service still owns a live
        # ``MemoryBackend``. Pull it out and publish it on
        # ``app_state`` so the DELETE memory controller and MCP tool
        # path see ``has_memory_backend == True`` -- otherwise an
        # injected-service deployment would surface as 501 / unsupported
        # even though a connected backend is right there.
        if app_state.has_training_service and not app_state.has_memory_backend:
            injected_backend = getattr(
                app_state.training_service,
                "_memory_backend",
                None,
            )
            if injected_backend is not None:
                app_state.set_memory_backend(injected_backend)

        # On-startup auto-wire: TrainingService.
        # Needs agent_registry, tool_invocation_tracker, and
        # performance_tracker (all wired at construction time).  Uses
        # InMemoryBackend for the memory layer; production callers
        # inject a real Mem0 backend via the training_service param.
        if (
            not app_state.has_training_service
            and effective_config is not None
            and effective_config.training.enabled
            and app_state.has_agent_registry
            and app_state.has_tool_invocation_tracker
        ):
            try:
                from synthorg.hr.training.factory import (  # noqa: PLC0415
                    build_training_service,
                )
                from synthorg.memory.backends.inmemory import (  # noqa: PLC0415
                    InMemoryBackend,
                )

                _perf = app_state._performance_tracker  # noqa: SLF001
                if _perf is not None:
                    _mem = InMemoryBackend()
                    await _mem.connect()
                    try:
                        _ts = build_training_service(
                            config=effective_config.training,
                            memory_backend=_mem,
                            tracker=_perf,
                            registry=app_state.agent_registry,
                            approval_store=app_state.approval_store,
                            tool_tracker=app_state.tool_invocation_tracker,
                        )
                        app_state.set_training_service(_ts)
                        # Expose the same backend to admin paths so
                        # ``DELETE /agents/{id}/memories/{id}`` and the
                        # ``delete_memory`` MCP tool can route through
                        # one connected backend instance per process.
                        if not app_state.has_memory_backend:
                            app_state.set_memory_backend(_mem)
                    except MemoryError, RecursionError:
                        await _mem.disconnect()
                        raise
                    except Exception:
                        await _mem.disconnect()
                        raise
                    _training_memory_backend = _mem
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
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
            except MemoryError, RecursionError:
                raise
            except Exception:  # noqa: S110 -- already logged via done-callback
                pass
        await _apply_bridge_config(app_state, effective_config)
        await _apply_security_timeout_interval(app_state, approval_timeout_scheduler)

        # Rebind the live ``MessageBusBridge`` to the now-wired
        # resolver. ``create_app`` captures the resolver eagerly when
        # the bridge is constructed; on the auto-wire path the
        # resolver is not yet available at that moment, so the bridge
        # is built with ``None`` and would otherwise read the
        # registered defaults forever.
        if bridge is not None and app_state.has_config_resolver:
            bridge.set_config_resolver(app_state.config_resolver)

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
            except MemoryError, RecursionError:
                raise
            except Exception:  # noqa: S110 -- already logged via done-callback
                pass
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
            except MemoryError, RecursionError:
                raise
            except Exception:  # noqa: S110 -- already logged via done-callback
                pass
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
        if app_state.webhook_event_bridge is not None:
            try:
                await app_state.webhook_event_bridge.start()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    phase="webhook_event_bridge_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        if app_state.health_prober_service is not None:
            try:
                await app_state.health_prober_service.start()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    phase="health_prober_service_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        if app_state.oauth_token_manager is not None:
            try:
                await app_state.oauth_token_manager.start()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    phase="oauth_token_manager_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        if app_state.escalation_sweeper is not None:
            try:
                await app_state.escalation_sweeper.start()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    phase="escalation_sweeper_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        if app_state.escalation_notify_subscriber is not None:
            try:
                await app_state.escalation_notify_subscriber.start()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
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
        if app_state.event_stream_hub is not None:
            try:
                (
                    idle_ttl,
                    janitor_interval,
                ) = await _resolve_event_stream_janitor_settings(app_state)
                await app_state.event_stream_hub.start(
                    idle_ttl_seconds=idle_ttl,
                    janitor_interval_seconds=janitor_interval,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    phase="event_stream_hub_start",
                    severity="non_fatal",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    async def on_shutdown() -> None:  # noqa: C901, PLR0912, PLR0915
        nonlocal _ticket_cleanup_task, _audit_retention_task
        nonlocal _webhook_cleanup_task
        nonlocal _auto_wired_dispatcher
        nonlocal _health_prober, _training_memory_backend
        # Disconnect training memory backend if auto-wired.
        if _training_memory_backend is not None:
            # If this backend was published to ``app_state.memory_backend``
            # at startup, clear the slot before disconnecting so a
            # subsequent re-entry of the lifespan can wire a fresh
            # connected backend without ``has_memory_backend`` reporting
            # a stale handle.
            shared = getattr(app_state, "_memory_backend", None)
            if shared is _training_memory_backend:
                app_state._memory_backend = None  # noqa: SLF001
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
        # Stop the EventStreamHub janitor before logging shutdown so
        # the event-loop shutdown sequence does not race the cancel.
        if app_state.event_stream_hub is not None:
            await _try_stop(
                app_state.event_stream_hub.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop event stream hub",
            )
        logger.info(API_APP_SHUTDOWN, version=__version__)
        if _health_prober is not None:
            await _try_stop(
                _health_prober.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop health prober",
            )
            _health_prober = None
        # Stop integration background services (reverse start order).
        if app_state.escalation_notify_subscriber is not None:
            await _try_stop(
                app_state.escalation_notify_subscriber.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop escalation notify subscriber",
            )
        if app_state.escalation_sweeper is not None:
            await _try_stop(
                app_state.escalation_sweeper.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop escalation sweeper",
            )
        # Cancel any unresolved pending futures so coroutines awaiting
        # operator decisions get a clean CancelledError (instead of
        # hanging past shutdown) and the registry map is emptied.
        if app_state.escalation_registry is not None:
            await _try_stop(
                app_state.escalation_registry.close(),
                API_APP_SHUTDOWN,
                "Failed to close escalation pending-futures registry",
            )
        if app_state.oauth_token_manager is not None:
            await _try_stop(
                app_state.oauth_token_manager.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop OAuth token manager",
            )
        if app_state.health_prober_service is not None:
            await _try_stop(
                app_state.health_prober_service.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop integration health prober",
            )
        if app_state.webhook_event_bridge is not None:
            await _try_stop(
                app_state.webhook_event_bridge.stop(),
                API_APP_SHUTDOWN,
                "Failed to stop webhook event bridge",
            )
        if app_state.has_tunnel_provider:
            await _try_stop(
                app_state.tunnel_provider.stop(),
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
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
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
            performance_tracker=app_state._performance_tracker,  # noqa: SLF001
            distributed_task_queue=app_state.distributed_task_queue,
        )
        if app_state.has_notification_dispatcher:
            await _try_stop(
                app_state.notification_dispatcher.aclose(),
                API_APP_SHUTDOWN,
                "Failed to stop notification dispatcher",
            )
        # Close A2A outbound HTTP client if wired.
        try:
            a2a_client_obj = app_state._a2a_client  # noqa: SLF001
            if a2a_client_obj is not None and hasattr(a2a_client_obj, "aclose"):
                await a2a_client_obj.aclose()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                API_APP_SHUTDOWN,
                phase="a2a_client_close",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    return [on_startup], [on_shutdown]
