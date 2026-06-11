# module-kind: orchestrator
"""Ordered service startup with reverse cleanup on failure.

``_safe_startup`` starts every core-scaffold service in dependency order and,
on any failure, tears down the already-started ones in reverse (via
:func:`synthorg.api.lifecycle_shared._cleanup_on_failure`). The persistence
initialisation helpers (migrations, JWT, catalog rebind, ontology) live here
too. Split out of ``api.lifecycle`` to keep each file under the size budget.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.api.api_core_state import ApiCoreStateSlice, auth_service_of
from synthorg.api.auth.secret import resolve_jwt_secret
from synthorg.api.auth.service import AuthService
from synthorg.api.auth.system_user import ensure_system_user
from synthorg.api.lifecycle_helpers.auth_store_autowire import wire_auth_stores
from synthorg.api.lifecycle_shared import _cleanup_on_failure
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.backup.models import BackupTrigger
from synthorg.backup.state import BackupStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import (
    IntegrationsStateSlice,
    connection_catalog_of,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.ontology.state import OntologyStateSlice
from synthorg.workers.state import RuntimeStateSlice

if TYPE_CHECKING:
    from synthorg.api.bus_bridge import MessageBusBridge
    from synthorg.backup.service import BackupService
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.communication.meeting.scheduler import MeetingScheduler
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
    from synthorg.settings.dispatcher import SettingsChangeDispatcher

logger = get_logger(__name__)


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
    # Resolve JWT secret before migrations so missing env vars fail fast (no
    # point running migrations if startup will abort anyway).
    if app_state.slice(ApiCoreStateSlice).auth_service is not None:
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
            app_state.wire(ApiCoreStateSlice, auth_service=AuthService(auth_config))
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

    # Bind the real MCP installations repository now that persistence is
    # connected. ``create_app`` intentionally left this slot empty when a
    # persistence backend was configured so the in-memory stub would not survive
    # startup as a shadow repo.
    if app_state.slice(IntegrationsStateSlice).mcp_installations_repo is None:
        try:
            app_state.wire(
                IntegrationsStateSlice,
                mcp_installations_repo=persistence.mcp_installations,
            )
        except Exception as exc:
            # The repo is required: ``create_app`` deliberately leaves the slot
            # empty when persistence is configured so the in-memory stub does
            # not survive into a real boot. If the wire-up fails, fail closed.
            log_exception_redacted(
                logger,
                API_APP_STARTUP,
                exc,
                note="Failed to wire persistence-backed MCP installations repo",
            )
            raise

    if app_state.slice(IntegrationsStateSlice).connection_catalog is not None:
        await _rebind_connection_catalog(persistence, app_state)

    await _wire_ontology_service(persistence, app_state)


async def _wire_ontology_service(
    persistence: PersistenceBackend,
    app_state: AppState,
) -> None:
    """Wire ontology after persistence connects; no-op if already wired."""
    # Short-circuit BEFORE calling ``auto_wire_ontology``: the factory allocates
    # repositories and caches that we'd otherwise drop without cleanup on a
    # lifespan re-entry when the slice is already wired.
    if app_state.slice(OntologyStateSlice).service is not None:
        return
    from synthorg.api.auto_wire import auto_wire_ontology  # noqa: PLC0415

    service = await auto_wire_ontology(app_state.config, persistence)
    if service is not None:
        app_state.wire(OntologyStateSlice, service=service)


async def _rebind_connection_catalog(
    persistence: PersistenceBackend,
    app_state: AppState,
) -> None:
    """Re-bind the ConnectionCatalog onto a persistence-backed repo.

    ``auto_wire_integrations`` runs before ``persistence.connect()``, so the
    catalog is initially seeded with an in-memory stub; without this re-bind,
    every ``POST /connections`` would write to a stub that the
    ``mcp_installations.connection_name`` foreign key cannot observe, surfacing
    as cross-store FK violations on install.

    ``AttributeError`` keeps the helper backend-agnostic for tests / backends
    without a ``connections`` accessor; any other property-getter failure
    surfaces via the warning path. A failure inside the catalog's own
    ``rebind_repository`` is fatal and logged at ERROR before re-raising.
    """
    try:
        persistent_connections = persistence.connections
    except AttributeError:
        return
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            note="Persistence backend connections accessor failed; "
            "catalog stays bound to the startup-window stub",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    try:
        await connection_catalog_of(app_state).rebind_repository(
            persistent_connections,
        )
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            note="Failed to re-bind ConnectionCatalog to persistence-backed repo",
            backend=type(persistence).__name__,
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
    ``MeetingScheduler``, ``SettingsChangeDispatcher``) leave ``_running=True``
    after the owning loop closes and cancels their tasks. Without this reset the
    next startup skips ``start()``, leaving the service non-functional.

    Handles both plural task collections (``_tasks: list[Task]``) and
    single-task services (``_task: Task | None``). No-op for ``MagicMock``
    instances and for services whose tasks are still alive.
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

    Executes in order; on failure, cleans up already-started components in
    reverse order before re-raising.

    Raises:
        RuntimeError: When the approval-timeout scheduler is unrestartable
            (re-raised after the reverse cleanup completes).
        Exception: Any other service start failure, re-raised after the reverse
            cleanup of the already-started services completes.
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
    runtime_slice = app_state.slice(RuntimeStateSlice)
    distributed_task_queue = runtime_slice.distributed_task_queue
    distributed_backend_services = runtime_slice.distributed_backend_services
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
            # Mark connected immediately so cleanup can disconnect if migrate()
            # or JWT resolution fails below.
            started_persistence = True
            await _init_persistence(persistence, app_state)
            try:
                await ensure_system_user(persistence, auth_service_of(app_state))
            except Exception as exc:
                logger.warning(
                    API_APP_STARTUP,
                    note="Failed to bootstrap system user",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

            # Auth repositories (session / lockout / refresh) live on the
            # connected persistence backend; extracted to keep this hook
            # readable.
            await wire_auth_stores(app_state, persistence)

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
            # Started AFTER the queue connects: the dead-letter consumer and
            # heartbeat subscriber pull/subscribe over the same NATS connection
            # the queue owns.
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
        # ``is not True`` (rather than ``not obj._running``) is deliberate: unit
        # tests pass ``MagicMock`` instances whose attributes return truthy
        # ``MagicMock`` objects. ``not MagicMock()`` evaluates to ``False``
        # (skipping start and breaking those tests), while
        # ``MagicMock() is not True`` correctly evaluates to ``True``. For real
        # services ``_running`` is a bool, so both forms agree.
        #
        # Task-liveness guard: for services whose background tasks get bound to
        # the event loop (bridge, meeting_scheduler), a prior TestClient's event
        # loop can close and cancel those tasks while ``_running`` still reads
        # ``True``. Detect dead tasks and flip ``_running`` back to ``False`` so
        # this startup actually restarts the service.
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
            # SettingsChangeDispatcher has a singular ``_task`` (not ``_tasks``).
            # Its ``_on_task_done`` callback *usually* resets ``_running`` when
            # the task dies, but on event-loop close the callback may not fire
            # reliably, leaving the dispatcher stuck with ``_running=True``.
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
            # Skip start() when the backup scheduler is already running
            # (shared-app test fixture re-enters startup). Also only flip
            # ``started_backup_service`` to ``True`` *after* a fresh ``start()``
            # completes, so ``_cleanup_on_failure()`` never stops a
            # previously-running shared service.
            _bs_scheduler = getattr(backup_service, "scheduler", None)
            _bs_already_running = getattr(_bs_scheduler, "is_running", False)
            try:
                if app_state.slice(BackupStateSlice).service is None:
                    app_state.wire(BackupStateSlice, service=backup_service)
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
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        API_APP_STARTUP,
                        note="Startup backup failed (non-fatal)",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
        if approval_timeout_scheduler is not None:
            try:
                app_state.wire(
                    ApprovalStateSlice,
                    timeout_scheduler=approval_timeout_scheduler,
                )
                await approval_timeout_scheduler.start()
                started_approval_timeout_scheduler = True
            except RuntimeError as exc:
                # ``ApprovalTimeoutScheduler.start()`` raises ``RuntimeError``
                # when a prior ``stop()`` timed out and the scheduler is now
                # unrestartable. The fresh instance rule applies: log without
                # the stack trace and propagate so startup fails closed.
                log_exception_redacted(
                    logger,
                    API_APP_STARTUP,
                    exc,
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
