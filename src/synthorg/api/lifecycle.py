"""Lifecycle helpers for service startup and shutdown.

Contains ``_safe_startup`` (ordered startup with reverse cleanup on
failure), ``_safe_shutdown`` (graceful ordered teardown), and their
supporting helpers.
"""

import asyncio
from typing import TYPE_CHECKING, Protocol

from synthorg.api.auth.secret import resolve_jwt_secret
from synthorg.api.auth.service import AuthService
from synthorg.api.auth.system_user import ensure_system_user
from synthorg.backup.models import BackupTrigger
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_SHUTDOWN, API_APP_STARTUP
from synthorg.persistence.auth_protocol import (
    LockoutRepository,  # noqa: TC001
    RefreshTokenRepository,  # noqa: TC001
    SessionRepository,  # noqa: TC001
)
from synthorg.providers.health_prober import ProviderHealthProber

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from synthorg.api.bus_bridge import MessageBusBridge
    from synthorg.api.state import AppState
    from synthorg.backup.service import BackupService
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.meeting.scheduler import MeetingScheduler
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
    from synthorg.settings.dispatcher import SettingsChangeDispatcher

logger = get_logger(__name__)


# Per-service shutdown budgets (seconds). The total budget is bounded so
# container orchestrators have headroom before SIGKILL kicks. The current
# Worst-case shutdown math:
#
#   drain (25)  +  task_engine outer (8 * 2 + 1 = 17)  +
#   meeting (2) +  perf (2)  +  backup (5)  +  settings (2)  +
#   bridge (2)  +  distributed (3)  +  bus (3)  +  persistence (5)  +
#   approval (1)
#   = 25 + 42 = ~67 s worst case if the drain is held for its full
#     budget AND every service uses its full budget. In practice the
#     drain runs concurrently with no service work and most services
#     return well under their cap. Realistic headline budget is 25
#     (drain) + ~26 s (services) = ~51 s.
#
# Internal constants by design: per-service shutdown budgets enforce a
# fixed total worst-case drain of ~67 s (drain 25 s + services 42 s,
# already inclusive of the drain budget below), matched in
# api/server.py by Litestar's 75 s graceful_shutdown to reserve ~8 s
# of headroom (75 - 67) before the orchestrator SIGKILLs the process.
# Recommended ``terminationGracePeriodSeconds: 75`` per
# ``docs/design/deployment.md``. Raising any individual budget
# narrows that 8 s headroom contract with the orchestrator and risks
# SIGKILL mid-teardown; not exposed to the settings registry because
# the shape of the contract -- not its operator-tunability -- is what
# the orchestrator depends on.
_TASK_ENGINE_SHUTDOWN_SECONDS: float = 8.0
_MEETING_SCHEDULER_SHUTDOWN_SECONDS: float = 2.0
_PERFORMANCE_TRACKER_SHUTDOWN_SECONDS: float = 2.0
_BACKUP_SHUTDOWN_SECONDS: float = 5.0
_SETTINGS_DISPATCHER_SHUTDOWN_SECONDS: float = 2.0
_BRIDGE_SHUTDOWN_SECONDS: float = 2.0
_DISTRIBUTED_QUEUE_SHUTDOWN_SECONDS: float = 3.0
_DISTRIBUTED_BUNDLE_SHUTDOWN_SECONDS: float = 3.0
_MESSAGE_BUS_SHUTDOWN_SECONDS: float = 3.0
_PERSISTENCE_SHUTDOWN_SECONDS: float = 5.0
_APPROVAL_TIMEOUT_SHUTDOWN_SECONDS: float = 1.0
_DRAIN_TIMEOUT_SECONDS: float = 25.0
"""Default budget for :class:`RequestDrainMiddleware`.

Maximum time the on_shutdown drain hook waits for in-flight HTTP
requests to complete after the drain gate flips. Exceeding this
budget is logged at WARNING (``API_APP_DRAIN_TIMEOUT``) and the
service-teardown sequence continues regardless.
"""


# Structural seam over the optional synthorg[distributed] JetStreamTaskQueue;
# consumers: _cleanup_on_failure, _safe_shutdown.
class _AsyncStartStop(Protocol):
    """Minimal async lifecycle Protocol used by the distributed task queue hook.

    The concrete type is ``synthorg.workers.claim.JetStreamTaskQueue``, but
    importing that here would force the optional ``synthorg[distributed]``
    extra to be installed even for deployments that never use the queue.
    A structural Protocol with ``start()``/``stop()`` gives the lifecycle
    helpers a real shape without the hard dependency.
    """

    async def start(self) -> None:
        """Open the connection / initialise resources."""
        ...

    async def stop(self) -> None:
        """Tear down the connection / release resources."""
        ...


async def _try_stop(
    coro: Awaitable[None],
    event: str,
    error_msg: str,
    *,
    timeout: float | None = None,  # noqa: ASYNC109 -- per-service shutdown budget
    service: str | None = None,
) -> bool:
    """Await *coro* inside a safe try/except, logging failures.

    ``MemoryError`` and ``RecursionError`` are re-raised immediately;
    all other exceptions are logged and swallowed so that sibling
    shutdown steps can still run.

    When *timeout* is set, a ``TimeoutError`` is logged at ERROR with
    the ``service`` label (when provided) and shutdown continues with
    the next service.  Services that hang past their per-service
    budget must not block the whole shutdown window.

    Returns ``True`` when *coro* completes without raising, ``False``
    when an exception was swallowed. Callers use this to guard
    "stopped" log lines so they only fire on actual success.
    """
    awaitable = asyncio.wait_for(coro, timeout=timeout) if timeout is not None else coro
    try:
        await awaitable
    except MemoryError, RecursionError:
        raise
    except TimeoutError as exc:
        from synthorg.observability.events.api import (  # noqa: PLC0415
            API_APP_SHUTDOWN_TIMEOUT,
        )

        # logger.error rather than logger.exception so TimeoutError
        # frame-locals never serialize the awaited coroutine's state
        # (which may include secret-bearing shutdown objects).
        logger.error(
            API_APP_SHUTDOWN_TIMEOUT,
            service=service,
            timeout_seconds=timeout,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            context=error_msg,
        )
        return False
    except Exception as exc:
        logger.warning(
            event,
            service=service,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            context=error_msg,
        )
        return False
    return True


async def _cleanup_on_failure(  # noqa: PLR0913
    *,
    persistence: PersistenceBackend | None,
    started_persistence: bool,
    message_bus: MessageBus | None,
    started_bus: bool,
    bridge: MessageBusBridge | None = None,
    started_bridge: bool = False,
    settings_dispatcher: SettingsChangeDispatcher | None = None,
    started_settings_dispatcher: bool = False,
    task_engine: TaskEngine | None = None,
    started_task_engine: bool = False,
    distributed_task_queue: _AsyncStartStop | None = None,
    started_distributed_task_queue: bool = False,
    distributed_backend_services: _AsyncStartStop | None = None,
    started_distributed_backend_services: bool = False,
    meeting_scheduler: MeetingScheduler | None = None,
    started_meeting_scheduler: bool = False,
    backup_service: BackupService | None = None,
    started_backup_service: bool = False,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None,
    started_approval_timeout_scheduler: bool = False,
) -> None:
    """Reverse cleanup on startup failure."""
    if started_approval_timeout_scheduler and approval_timeout_scheduler is not None:
        await _try_stop(
            approval_timeout_scheduler.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop approval timeout scheduler",
        )
    if started_backup_service and backup_service is not None:
        await _try_stop(
            backup_service.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop backup service",
        )
    if started_meeting_scheduler and meeting_scheduler is not None:
        await _try_stop(
            meeting_scheduler.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop meeting scheduler",
        )
    if started_task_engine and task_engine is not None:
        await _try_stop(
            task_engine.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop task engine",
        )
    if started_settings_dispatcher and settings_dispatcher is not None:
        await _try_stop(
            settings_dispatcher.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop settings dispatcher",
        )
    if started_bridge and bridge is not None:
        await _try_stop(
            bridge.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop message bus bridge",
        )
    if (
        started_distributed_backend_services
        and distributed_backend_services is not None
    ):
        await _try_stop(
            distributed_backend_services.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop distributed backend services",
        )
    if started_distributed_task_queue and distributed_task_queue is not None:
        logger.info(
            API_APP_STARTUP,
            service="distributed_task_queue",
            phase="stopping_on_cleanup",
        )
        ok = await _try_stop(
            distributed_task_queue.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop distributed task queue",
        )
        if ok:
            logger.info(
                API_APP_STARTUP,
                service="distributed_task_queue",
                phase="stopped_on_cleanup",
            )
    if started_bus and message_bus is not None:
        await _try_stop(
            message_bus.stop(),
            API_APP_STARTUP,
            "Cleanup: failed to stop message bus",
        )
    if started_persistence and persistence is not None:
        await _try_stop(
            persistence.disconnect(),
            API_APP_STARTUP,
            "Cleanup: failed to disconnect persistence",
        )


async def _init_persistence(
    persistence: PersistenceBackend,
    app_state: AppState,
) -> None:
    """Run migrations and resolve JWT secret on an already-connected backend.

    Must only be called after ``persistence.connect()`` has succeeded.

    Args:
        persistence: Connected persistence backend.
        app_state: Application state for auth service injection.
    """
    # Resolve JWT secret before migrations so missing env vars fail fast
    # (no point running migrations if startup will abort anyway).
    if app_state.has_auth_service:
        logger.info(
            API_APP_STARTUP,
            note="Auth service already configured, skipping JWT secret resolution",
        )
    else:
        try:
            secret = resolve_jwt_secret()
            auth_config = app_state.config.api.auth.with_secret(
                secret,
            )
            app_state.set_auth_service(AuthService(auth_config))
        except Exception as exc:
            logger.warning(
                API_APP_STARTUP,
                note="Failed to resolve JWT secret",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    try:
        await persistence.migrate()
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Failed to run persistence migrations",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise

    # Bind the real MCP installations repository now that persistence
    # is connected.  ``create_app`` intentionally left this slot empty
    # when a persistence backend was configured so the in-memory stub
    # would not survive startup as a shadow repo.
    if not app_state.has_mcp_installations_repo:
        try:
            app_state.set_mcp_installations_repo(persistence.mcp_installations)
        except Exception as exc:
            # The repo is required: ``create_app`` deliberately leaves
            # the slot empty when persistence is configured so the
            # in-memory stub does not survive into a real boot. If the
            # wire-up fails, the app would serve traffic with no MCP
            # installations repo wired at all -- fail closed instead.
            logger.error(
                API_APP_STARTUP,
                note="Failed to wire persistence-backed MCP installations repo",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    if app_state.has_connection_catalog:
        await _rebind_connection_catalog(persistence, app_state)

    await _wire_ontology_service(persistence, app_state)


async def _wire_ontology_service(
    persistence: PersistenceBackend,
    app_state: AppState,
) -> None:
    """Wire ontology after persistence connects; no-op if already wired."""
    from synthorg.api.auto_wire import auto_wire_ontology  # noqa: PLC0415

    service = await auto_wire_ontology(app_state.config, persistence)
    if service is not None:
        try:
            app_state.set_ontology_service(service)
        except RuntimeError:
            return


async def _rebind_connection_catalog(
    persistence: PersistenceBackend,
    app_state: AppState,
) -> None:
    """Re-bind the ConnectionCatalog onto a persistence-backed repo.

    ``auto_wire_integrations`` runs before ``persistence.connect()``,
    so the catalog is initially seeded with an in-memory stub; without
    this re-bind, every ``POST /connections`` would write to a stub
    that the ``mcp_installations.connection_name`` foreign key cannot
    observe, surfacing as cross-store FK violations on install.

    ``AttributeError`` keeps the helper backend-agnostic for tests /
    backends without a ``connections`` accessor (the in-memory stub
    stays bound for read-only flows); any other property-getter
    failure surfaces via the warning path so the operator sees the
    cause instead of an opaque startup crash. A failure inside the
    catalog's own ``rebind_repository`` is fatal and logged at ERROR
    before re-raising, so root-cause diagnosis is not blind.
    """
    try:
        persistent_connections = persistence.connections
    except AttributeError:
        return
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Persistence backend connections accessor failed; "
            "catalog stays bound to the startup-window stub",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    try:
        await app_state.connection_catalog.rebind_repository(
            persistent_connections,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.error(
            API_APP_STARTUP,
            note="Failed to re-bind ConnectionCatalog to persistence-backed repo",
            backend=type(persistence).__name__,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(
        API_APP_STARTUP,
        note="Re-bound ConnectionCatalog to persistence-backed repo",
        backend=type(persistence).__name__,
    )


def _reset_if_tasks_dead(
    obj: object,
    running_attr: str,
    tasks_attr: str,
) -> None:
    """Flip *running_attr* to ``False`` when all background tasks are dead.

    Services with tasks bound to the event loop (``MessageBusBridge``,
    ``MeetingScheduler``, ``SettingsChangeDispatcher``) leave
    ``_running=True`` after the owning loop closes and cancels their
    tasks.  Without this reset the next startup skips ``start()``,
    leaving the service non-functional.

    Handles both plural task collections (``_tasks: list[Task]``) and
    single-task services (``_task: Task | None``).  No-op for
    ``MagicMock`` instances and for services whose tasks are still
    alive.
    """
    tasks_or_task = getattr(obj, tasks_attr, None)
    if tasks_or_task is None:
        return
    if isinstance(tasks_or_task, list):
        if not tasks_or_task or not all(t.done() for t in tasks_or_task):
            return
        try:
            setattr(obj, running_attr, False)
        except AttributeError:
            # ``__slots__``-only class without the running attr -- skip.
            return
        tasks_or_task.clear()
        return
    if isinstance(tasks_or_task, asyncio.Task):
        if not tasks_or_task.done():
            return
        try:
            setattr(obj, running_attr, False)
        except AttributeError:
            return
        try:
            setattr(obj, tasks_attr, None)
        except AttributeError:
            return


async def _safe_startup(  # noqa: PLR0913
    persistence: PersistenceBackend | None,
    message_bus: MessageBus | None,
    bridge: MessageBusBridge | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
    app_state: AppState,
) -> None:
    """Start all services in order, with reverse cleanup on failure.

    Executes in order; on failure, cleans up already-started
    components in reverse order before re-raising.
    """
    started_bus = False
    started_bridge = False
    started_persistence = False
    started_settings_dispatcher = False
    started_task_engine = False
    started_distributed_task_queue = False
    started_distributed_backend_services = False
    started_meeting_scheduler = False
    started_backup_service = False
    started_approval_timeout_scheduler = False
    distributed_task_queue = app_state.distributed_task_queue
    distributed_backend_services = app_state.distributed_backend_services
    try:
        if persistence is not None:
            try:
                await persistence.connect()
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to connect persistence",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            # Mark connected immediately so cleanup can disconnect
            # if migrate() or JWT resolution fails below.
            started_persistence = True
            await _init_persistence(persistence, app_state)
            try:
                await ensure_system_user(persistence, app_state.auth_service)
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to bootstrap system user",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

            # Auth repositories live on the persistence backend.
            # Sessions and refresh tokens are properties; lockouts are
            # built via ``build_lockouts(auth_config)`` because they
            # need the operator's threshold/window/duration policy.
            if not app_state.has_session_store:
                session_store: SessionRepository = persistence.sessions
                await session_store.load_revoked()
                app_state.set_session_store(session_store)
                logger.info(
                    API_APP_STARTUP,
                    note="Session store initialized",
                    backend=type(session_store).__name__,
                )

            auth_cfg = (
                app_state.config.api.auth if app_state.config is not None else None
            )
            if auth_cfg is not None and not app_state.has_lockout_store:
                try:
                    lockout_store: LockoutRepository = persistence.build_lockouts(
                        auth_cfg,
                    )
                    await lockout_store.load_locked()
                    app_state.set_lockout_store(lockout_store)
                    logger.info(
                        API_APP_STARTUP,
                        note="Lockout store initialized",
                        backend=type(lockout_store).__name__,
                    )
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    logger.error(
                        API_APP_STARTUP,
                        note="Lockout store initialization failed",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )

            if not app_state.has_refresh_store:
                try:
                    refresh_store: RefreshTokenRepository = persistence.refresh_tokens
                    app_state.set_refresh_store(refresh_store)
                    logger.info(
                        API_APP_STARTUP,
                        note="Refresh-token store initialized",
                        backend=type(refresh_store).__name__,
                    )
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    logger.error(
                        API_APP_STARTUP,
                        note="Refresh-token store initialization failed",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )

        if message_bus is not None:
            try:
                await message_bus.start()
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start message bus",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            started_bus = True
        if distributed_task_queue is not None:
            try:
                await distributed_task_queue.start()
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start distributed task queue",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            started_distributed_task_queue = True
            logger.info(
                API_APP_STARTUP,
                service="distributed_task_queue",
                phase="started",
            )
        if distributed_backend_services is not None:
            # Started AFTER the queue connects: the dead-letter consumer
            # and heartbeat subscriber pull/subscribe over the same
            # NATS connection the queue owns.
            try:
                await distributed_backend_services.start()
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start distributed backend services",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            started_distributed_backend_services = True
            logger.info(
                API_APP_STARTUP,
                service="distributed_backend_services",
                phase="started",
            )
        # ``is not True`` (rather than ``not obj._running``) is deliberate:
        # unit tests pass ``MagicMock`` instances whose attributes return
        # truthy ``MagicMock`` objects.  ``not MagicMock()`` evaluates to
        # ``False`` (skipping start and breaking those tests), while
        # ``MagicMock() is not True`` correctly evaluates to ``True``.
        # For real services ``_running`` is a bool, so both forms agree.
        #
        # Task-liveness guard: for services whose background tasks get
        # bound to the event loop (bridge, meeting_scheduler), a prior
        # TestClient's event loop can close and cancel those tasks
        # while ``_running`` still reads ``True``.  Detect dead tasks
        # and flip ``_running`` back to ``False`` so this startup
        # actually restarts the service.  ``MagicMock`` instances have
        # no real ``_tasks`` list, so this path is a no-op for them.
        if bridge is not None:
            _reset_if_tasks_dead(bridge, "_running", "_tasks")
        if bridge is not None and getattr(bridge, "_running", None) is not True:
            try:
                await bridge.start()
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start message bus bridge",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            started_bridge = True
        if settings_dispatcher is not None:
            # SettingsChangeDispatcher has a singular ``_task`` (not
            # ``_tasks``).  Its ``_on_task_done`` callback *usually*
            # resets ``_running`` when the task dies, but on event-loop
            # close the callback may not fire reliably, leaving the
            # dispatcher stuck with ``_running=True`` and no live task.
            _reset_if_tasks_dead(settings_dispatcher, "_running", "_task")
        _sd_running = getattr(settings_dispatcher, "_running", None)
        if settings_dispatcher is not None and _sd_running is not True:
            try:
                await settings_dispatcher.start()
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start settings dispatcher",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            started_settings_dispatcher = True
        if task_engine is not None and task_engine.is_running is not True:
            try:
                await task_engine.start()
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start task engine",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            started_task_engine = True
        if meeting_scheduler is not None:
            _reset_if_tasks_dead(meeting_scheduler, "_running", "_tasks")
        _ms_running = getattr(meeting_scheduler, "running", None)
        if meeting_scheduler is not None and _ms_running is not True:
            try:
                await meeting_scheduler.start()
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start meeting scheduler",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            started_meeting_scheduler = True
        if backup_service is not None:
            # Skip start() when the backup scheduler is already
            # running (shared-app test fixture re-enters startup).
            # Also only flip ``started_backup_service`` to ``True``
            # *after* a fresh ``start()`` completes, so
            # ``_cleanup_on_failure()`` never stops a
            # previously-running shared service.
            _bs_scheduler = getattr(backup_service, "scheduler", None)
            _bs_already_running = getattr(_bs_scheduler, "is_running", False)
            try:
                if not app_state.has_backup_service:
                    app_state.set_backup_service(backup_service)
                if not _bs_already_running:
                    await backup_service.start()
                    started_backup_service = True
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start backup service",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

            # Create startup backup if configured
            if backup_service.on_startup:
                try:
                    await backup_service.create_backup(
                        BackupTrigger.STARTUP,
                    )
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    logger.warning(
                        API_APP_STARTUP,
                        note="Startup backup failed (non-fatal)",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
        if approval_timeout_scheduler is not None:
            try:
                app_state.set_approval_timeout_scheduler(
                    approval_timeout_scheduler,
                )
                await approval_timeout_scheduler.start()
                started_approval_timeout_scheduler = True
            except RuntimeError as exc:
                # ``ApprovalTimeoutScheduler.start()`` raises
                # ``RuntimeError`` when a prior ``stop()`` timed out
                # and the scheduler is now unrestartable. The fresh
                # instance rule applies: log without the stack trace
                # (the underlying cause was already logged at stop
                # time) and propagate so startup fails closed.
                logger.error(
                    API_APP_STARTUP,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="Approval timeout scheduler is unrestartable",
                )
                raise
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to start approval timeout scheduler",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
    except Exception:
        await _cleanup_on_failure(
            persistence=persistence,
            started_persistence=started_persistence,
            message_bus=message_bus,
            started_bus=started_bus,
            bridge=bridge,
            started_bridge=started_bridge,
            settings_dispatcher=settings_dispatcher,
            started_settings_dispatcher=started_settings_dispatcher,
            task_engine=task_engine,
            started_task_engine=started_task_engine,
            distributed_task_queue=distributed_task_queue,
            started_distributed_task_queue=started_distributed_task_queue,
            distributed_backend_services=distributed_backend_services,
            started_distributed_backend_services=started_distributed_backend_services,
            meeting_scheduler=meeting_scheduler,
            started_meeting_scheduler=started_meeting_scheduler,
            backup_service=backup_service,
            started_backup_service=started_backup_service,
            approval_timeout_scheduler=approval_timeout_scheduler,
            started_approval_timeout_scheduler=started_approval_timeout_scheduler,
        )
        raise


async def _safe_shutdown(  # noqa: PLR0913
    task_engine: TaskEngine | None,
    meeting_scheduler: MeetingScheduler | None,
    backup_service: BackupService | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
    settings_dispatcher: SettingsChangeDispatcher | None,
    bridge: MessageBusBridge | None,
    message_bus: MessageBus | None,
    persistence: PersistenceBackend | None,
    performance_tracker: PerformanceTracker | None = None,
    distributed_task_queue: _AsyncStartStop | None = None,
    distributed_backend_services: _AsyncStartStop | None = None,
) -> None:
    """Stop services in reverse startup order.

    Approval timeout scheduler first, then meeting scheduler
    (depends on orchestrator), then task engine so it can drain queued
    mutations and publish final snapshots through the still-running
    bridge. The distributed task queue stops after the engine so
    in-flight observer callbacks can still publish their final claims.
    Performance tracker closes after task engine (sampling is
    triggered by task events). Backup runs before persistence
    disconnect so shutdown backup can still access the DB.
    """
    if approval_timeout_scheduler is not None:
        # Inner timeout sets the scheduler's ``_stop_failed`` flag on
        # drain timeout so a subsequent ``start()`` raises rather than
        # spawning a duplicate task on top of an in-flight cancelled
        # one. The outer ``_try_stop`` budget exceeds the inner so the
        # unrestartable guard actually fires before cancellation.
        await _try_stop(
            approval_timeout_scheduler.stop(
                timeout=_APPROVAL_TIMEOUT_SHUTDOWN_SECONDS,
            ),
            API_APP_SHUTDOWN,
            "Failed to stop approval timeout scheduler",
            timeout=_APPROVAL_TIMEOUT_SHUTDOWN_SECONDS * 2.0 + 1.0,
            service="approval_timeout_scheduler",
        )
    if meeting_scheduler is not None:
        await _try_stop(
            meeting_scheduler.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop meeting scheduler",
            timeout=_MEETING_SCHEDULER_SHUTDOWN_SECONDS,
            service="meeting_scheduler",
        )
    if task_engine is not None:
        # ``TaskEngine.stop`` uses an internal hard deadline of
        # ``2 * effective_timeout`` before it sets ``_unrestartable``
        # and raises ``TimeoutError``. The outer ``_try_stop`` wait
        # must exceed that bound (plus a small slack) so a hung drain
        # actually reaches the unrestartable guard instead of being
        # cancelled by the outer wait -- otherwise a later ``start()``
        # could attach a second loop pair on top of orphaned tasks.
        await _try_stop(
            task_engine.stop(timeout=_TASK_ENGINE_SHUTDOWN_SECONDS),
            API_APP_SHUTDOWN,
            "Failed to stop task engine",
            timeout=_TASK_ENGINE_SHUTDOWN_SECONDS * 2.0 + 1.0,
            service="task_engine",
        )
    if performance_tracker is not None:
        await _try_stop(
            performance_tracker.aclose(),
            API_APP_SHUTDOWN,
            "Failed to close performance tracker",
            timeout=_PERFORMANCE_TRACKER_SHUTDOWN_SECONDS,
            service="performance_tracker",
        )
    if backup_service is not None:
        if backup_service.on_shutdown:
            try:
                await backup_service.create_backup(
                    BackupTrigger.SHUTDOWN,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    API_APP_SHUTDOWN,
                    note="Shutdown backup failed (non-fatal)",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        await _try_stop(
            backup_service.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop backup service",
            timeout=_BACKUP_SHUTDOWN_SECONDS,
            service="backup_service",
        )
    if settings_dispatcher is not None:
        await _try_stop(
            settings_dispatcher.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop settings dispatcher",
            timeout=_SETTINGS_DISPATCHER_SHUTDOWN_SECONDS,
            service="settings_dispatcher",
        )
    if bridge is not None:
        await _try_stop(
            bridge.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop message bus bridge",
            timeout=_BRIDGE_SHUTDOWN_SECONDS,
            service="bus_bridge",
        )
    # Backend distributed-path bundle stops before the queue: its
    # dead-letter consumer + heartbeat subscriber read over the queue's
    # NATS connection, so they must release it before the queue drains.
    if distributed_backend_services is not None:
        await _try_stop(
            distributed_backend_services.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop distributed backend services",
            timeout=_DISTRIBUTED_BUNDLE_SHUTDOWN_SECONDS,
            service="distributed_backend_services",
        )
    # Distributed task queue stops after bridge but before the bus so
    # the NATS connection it shares is still alive during drain. This
    # mirrors the exact inverse of the startup order: bus -> queue ->
    # bridge -> ... -> task_engine.
    if distributed_task_queue is not None:
        logger.info(
            API_APP_SHUTDOWN,
            service="distributed_task_queue",
            phase="stopping",
        )
        ok = await _try_stop(
            distributed_task_queue.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop distributed task queue",
            timeout=_DISTRIBUTED_QUEUE_SHUTDOWN_SECONDS,
            service="distributed_task_queue",
        )
        if ok:
            logger.info(
                API_APP_SHUTDOWN,
                service="distributed_task_queue",
                phase="stopped",
            )
    if message_bus is not None:
        await _try_stop(
            message_bus.stop(),
            API_APP_SHUTDOWN,
            "Failed to stop message bus",
            timeout=_MESSAGE_BUS_SHUTDOWN_SECONDS,
            service="message_bus",
        )
    if persistence is not None:
        await _try_stop(
            persistence.disconnect(),
            API_APP_SHUTDOWN,
            "Failed to disconnect persistence",
            timeout=_PERSISTENCE_SHUTDOWN_SECONDS,
            service="persistence",
        )


async def _maybe_start_health_prober(
    app_state: AppState,
) -> ProviderHealthProber | None:
    """Start the health prober if provider tracking is available.

    Non-fatal: logs and returns None on failure so the application
    continues serving requests without health probing.

    Args:
        app_state: Application state.  Requires
            ``provider_health_tracker`` and ``config_resolver``;
            optionally uses ``provider_management`` for SSRF policy.

    Returns:
        The started prober instance, or None if preconditions are
        not met or startup fails.
    """
    if not (app_state.has_provider_health_tracker and app_state.has_config_resolver):
        logger.debug(
            API_APP_STARTUP,
            note="Health prober skipped: tracker or resolver not available",
        )
        return None
    try:
        policy_loader = (
            app_state.provider_management.get_discovery_policy
            if app_state.has_provider_management
            else None
        )
        prober = ProviderHealthProber(
            health_tracker=app_state.provider_health_tracker,
            config_resolver=app_state.config_resolver,
            discovery_policy_loader=policy_loader,
        )
        await prober.start()
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Health prober startup failed (non-fatal)",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    return prober
