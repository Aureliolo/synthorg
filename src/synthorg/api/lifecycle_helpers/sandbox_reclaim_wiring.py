# module-kind: code
"""Wiring for the sandbox reclamation sweep.

Boot reconciliation runs once and stamps the slice, and that is right for its
question: at boot, a container with no row belongs to a dead predecessor.
This sweep asks a different question of a live process, whether the run that
owns each held container has finished, so it runs on a cadence and is driven
by the same reconciler shape run recovery uses: a first pass now, then the
scheduler.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.state import SettingsStateSlice
from synthorg.tools.sandbox.lifecycle.config import LifecycleStrategy
from synthorg.tools.sandbox.reclaim import (
    ReclaimableSandbox,
    SandboxOwnerReclaimer,
    SandboxReclaimScheduler,
    boot_trigger,
    describe_outcome,
)
from synthorg.tools.state import ToolsStateSlice
from synthorg.workers.execution_service import AgentEngineExecutionService
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)


async def wire_sandbox_reclaim(app_state: AppState) -> None:
    """Run the first reclamation pass, then start its cadence.

    Idempotent for re-entered lifespans: returns early when a scheduler is
    already published.

    Raises:
        SubsystemDeclinedError: When a collaborator the sweep reads through
            is absent, named so the status surface can report which.
    """
    if app_state.slice(ToolsStateSlice).sandbox_reclaim_scheduler is not None:
        return
    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None or not persistence.is_connected:
        msg = "no connected persistence backend; an owner's run state is read from it"
        raise SubsystemDeclinedError(msg)
    service = app_state.slice(RuntimeStateSlice).worker_execution_service
    if not isinstance(service, AgentEngineExecutionService):
        msg = (
            "no agent-runtime execution service; it is what holds the sandbox "
            "backend and the lifecycle it reuses containers under"
        )
        raise SubsystemDeclinedError(msg)
    backend = service.sandbox_backend
    if backend is None or not isinstance(backend, ReclaimableSandbox):
        msg = "no reusable sandbox backend; nothing holds a container past a call"
        raise SubsystemDeclinedError(msg)
    if service.lifecycle_strategy_kind is LifecycleStrategy.PER_CALL:
        msg = "per-call sandbox lifecycle; no container outlives its command"
        raise SubsystemDeclinedError(msg)

    reclaimer = SandboxOwnerReclaimer(
        backend=backend,
        strategy_kind=service.lifecycle_strategy_kind,
        tasks=persistence.tasks,
    )
    outcome = await reclaimer.reconcile(trigger=boot_trigger())
    scheduler = SandboxReclaimScheduler(
        reclaimer,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    await scheduler.start()
    try:
        app_state.wire(ToolsStateSlice, sandbox_reclaim_scheduler=scheduler)
    except Exception:
        await scheduler.stop()
        raise
    logger.info(
        API_APP_STARTUP,
        service="sandbox_reclaim",
        note="wired",
        strategy=service.lifecycle_strategy_kind,
        **describe_outcome(outcome),
    )


async def unwire_sandbox_reclaim(app_state: AppState) -> None:
    """Stop the sweep and drop it from the slice."""
    scheduler = app_state.slice(ToolsStateSlice).sandbox_reclaim_scheduler
    if scheduler is None:
        return
    try:
        await scheduler.stop()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # The slice still drops the scheduler: leaving a stopped-or-not one
        # published would report the sweep up while a rebuild waits on it.
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="sandbox_reclaim",
            note="sweep scheduler stop failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    tools_slice = app_state.slice(ToolsStateSlice)
    app_state.swap_slice(
        tools_slice.model_copy(update={"sandbox_reclaim_scheduler": None})
    )
    logger.info(API_APP_STARTUP, service="sandbox_reclaim", note="unwired")


__all__ = ["unwire_sandbox_reclaim", "wire_sandbox_reclaim"]
