# module-kind: code
"""Boot reconciliation of sandbox containers against the Docker daemon.

A backend that dies mid-task leaves its sandbox containers running with
nothing left to own them: the in-memory tracking dict went with the process,
and no later pass looks for them. They keep their workspace mount, keep their
anonymous volume, and keep the image they were created from pinned, which is
what turns a crash into disk that no shipped command can reclaim.

This is the pass that closes that loop, and it runs at boot for a reason: at
that moment this process cannot yet have created a sandbox of its own, so a
container carrying this deployment's label belongs to a predecessor that is
demonstrably gone. Three independent guards keep the verdict safe, and each
covers a gap the others leave: the deployment label rules out another
backend on the same daemon, the tracked-container rows rule out a peer on
this same database, and the process-start cutoff rules out anything created
after we began, whatever order the reconciler happens to activate things in.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


def _boot_epoch_seconds(app_state: AppState) -> float:
    """Return the wall-clock epoch at which this process started.

    ``AppState.startup_time`` is a monotonic reading, which cannot be
    compared with the daemon's epoch-based container creation times. The
    elapsed monotonic span is the part that is trustworthy, so it is
    subtracted from the current wall clock to place boot on the same scale.

    Args:
        app_state: Application state carrying the clock and startup mark.

    Returns:
        Epoch seconds at process start.
    """
    elapsed = app_state.clock.monotonic() - app_state.startup_time
    return app_state.clock.now().timestamp() - elapsed


async def wire_sandbox_reconciliation(app_state: AppState) -> None:
    """Reconcile tracked sandbox containers with the daemon, once per boot.

    Idempotent: the slice stamp is the liveness answer, so a repeat pass
    returns immediately rather than sweeping twice.

    Raises:
        SubsystemDeclinedError: No persistence backend (the tracking rows
            live there), or the Docker daemon could not be reached.
    """
    import aiodocker  # noqa: PLC0415

    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.tools.sandbox.deployment_identity import (  # noqa: PLC0415
        deployment_id_for,
    )
    from synthorg.tools.sandbox.docker_reconcile_client import (  # noqa: PLC0415
        AiodockerReconcileClient,
    )
    from synthorg.tools.sandbox.reconciliation import (  # noqa: PLC0415
        reconcile_tracked_containers,
    )
    from synthorg.tools.state import ToolsStateSlice  # noqa: PLC0415

    if app_state.slice(ToolsStateSlice).sandbox_reconciled_at is not None:
        return

    persistence = app_state.slice(PersistenceStateSlice).backend
    if persistence is None or not persistence.is_connected:
        msg = "no connected persistence backend; container tracking is persisted"
        raise SubsystemDeclinedError(msg)

    reconciled_at = app_state.clock.now()
    workspace_root = agent_workspace_root_of(app_state)
    client = aiodocker.Docker()
    try:
        outcome = await reconcile_tracked_containers(
            repo=persistence.tracked_containers,
            docker=AiodockerReconcileClient(client),
            deployment_id=deployment_id_for(workspace_root),
            started_at=_boot_epoch_seconds(app_state),
        )
    except Exception as exc:
        reraise_critical(exc)
        # Declining rather than stamping: an unreachable daemon is a
        # condition that resolves on its own once it starts, and the next
        # reconciler pass should retry. Stamping here would retire the
        # question and leave the orphans for the life of the process.
        msg = (
            "docker daemon unreachable; sandbox orphans cannot be reconciled "
            f"({type(exc).__name__}: {safe_error_description(exc)})"
        )
        raise SubsystemDeclinedError(msg) from exc
    finally:
        try:
            await client.close()
        except Exception as close_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(close_exc)
            logger.warning(
                API_APP_STARTUP,
                service="sandbox_reconciliation",
                note="docker client close failed",
                error_type=type(close_exc).__name__,
                error=safe_error_description(close_exc),
            )

    app_state.wire(ToolsStateSlice, sandbox_reconciled_at=reconciled_at)
    logger.info(
        API_APP_STARTUP,
        service="sandbox_reconciliation",
        kept=len(outcome.kept),
        stale_rows_dropped=len(outcome.db_only_dropped),
        orphans_removed=len(outcome.docker_only_killed),
        foreign_skipped=len(outcome.foreign_skipped),
    )


__all__ = ["wire_sandbox_reconciliation"]
