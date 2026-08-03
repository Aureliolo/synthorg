# module-kind: code
"""Post-setup runtime wiring: provider reload, agent bootstrap, hot-swap.

Mutates :class:`AppState` to bring the full agent runtime online after
provider configuration without a process restart. Also owns the two
setup-flow concurrency primitives (``AGENT_LOCK`` / ``COMPLETE_LOCK``)
whose documented acquisition order is ``COMPLETE_LOCK -> AGENT_LOCK``.
"""

import asyncio

from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.state import HrStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.setup import (
    SETUP_AGENT_BOOTSTRAP_FAILED,
    SETUP_FEATURE_REWIRE_FAILED,
    SETUP_PROVIDER_RELOAD_FAILED,
)
from synthorg.organization.settings_write_lock import ORG_SETTINGS_WRITE_LOCK
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

# Serializes read-modify-write on the company-structure settings blob. The
# canonical instance lives in the organization layer so the MCP TeamService
# shares it; re-exported here as AGENT_LOCK for the setup + team controllers.
AGENT_LOCK = ORG_SETTINGS_WRITE_LOCK

# Module-level lock: serializes the entire /setup/complete flow so two
# concurrent clients cannot both pass the ``setup_complete=false`` check
# and then race on reinit + flag write.
COMPLETE_LOCK = asyncio.Lock()


async def post_setup_reinit(app_state: AppState) -> None:
    """Reload providers and bootstrap agents after setup completion.

    Raises on failure so the caller can keep ``setup_complete=false``
    when reinit cannot finish; a half-configured runtime presenting
    itself as "complete" is worse than a clear error the operator can
    retry after fixing the underlying provider config.

    The matching call site in
    :func:`SetupCompletionController.complete_setup` only persists the
    completion flag when this function returns without raising.

    Args:
        app_state: Application state containing services.

    Raises:
        Exception: Raised on the corresponding failure path.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return

    # 1. Reload provider registry from persisted config.
    try:
        from synthorg.api.lifecycle_helpers.provider_registry_reload import (  # noqa: PLC0415
            reload_persisted_provider_registry,
        )

        await reload_persisted_provider_registry(app_state)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            SETUP_PROVIDER_RELOAD_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise

    # 2. Bootstrap agents into runtime registry.
    agent_registry = app_state.slice(HrStateSlice).agent_registry
    if agent_registry is not None:
        try:
            from synthorg.api.bootstrap import (  # noqa: PLC0415
                bootstrap_agents,
            )

            await bootstrap_agents(
                config_resolver=config_resolver_of(app_state),
                agent_registry=agent_registry,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETUP_AGENT_BOOTSTRAP_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    # 3. Rebuild + hot-swap BOTH runtime services so a provider added
    #    after an empty-company start wakes the whole runtime live.
    await _rebuild_runtime_services(app_state)

    # 4. Reconcile: every provider-gated subsystem a boot-empty start left
    #    waiting (charter needs a provider for its interview LLM) now has its
    #    dependency, so one level-triggered pass brings them up. This is the
    #    same pass boot and the periodic resync run, not a parallel list of
    #    what setup happens to know about.
    await _reconcile_post_setup(app_state)


async def _reconcile_post_setup(app_state: AppState) -> None:
    """Run one reconcile pass and refuse to complete over a failed subsystem.

    The reconciler records a failing activation and carries on, which is right
    for a periodic sweep: one broken subsystem must not stop the others coming
    up. Setup completion is the opposite case. It is a one-shot answer to "is
    this deployment configured", so a subsystem that raised during this pass
    means the answer is no, and persisting ``setup_complete=true`` over it
    would hide the fault behind a green setup screen.

    Expected degradation does NOT reach here: a subsystem whose dependency is
    absent reads WAITING, not FAILED, exactly as at boot.

    A deferred report is refused for the same reason. Its statuses are a
    snapshot of a pass still running elsewhere, so no failure in it means only
    that the pass has not reached that subsystem yet, which is not an answer to
    the question completion is asking.

    Raises:
        SubsystemActivationError: When any subsystem's activation raised, or
            when the pass itself could not run or was deferred.
    """
    from synthorg.api.subsystems.errors import (  # noqa: PLC0415
        SubsystemActivationError,
    )
    from synthorg.api.subsystems.runtime import reconcile_subsystems  # noqa: PLC0415

    report = await reconcile_subsystems(app_state, trigger="post_setup")
    if report is None or report.deferred:
        # Kept apart from the failure list below: a pass that never ran is a
        # different fault from a subsystem that raised, and folding it in as a
        # pseudo-name told the operator a subsystem called "the pass itself"
        # had failed.
        deferred = "was deferred to a pass already in flight"
        reason = deferred if report else "could not run"
        logger.warning(SETUP_FEATURE_REWIRE_FAILED, pass_outcome=reason)
        msg = f"the reconcile pass after setup {reason}, so nothing was verified"
        raise SubsystemActivationError(msg)
    if report.failed:
        failed = ", ".join(report.failed)
        logger.warning(SETUP_FEATURE_REWIRE_FAILED, subsystems=failed)
        msg = f"subsystems failed to activate after setup: {failed}"
        raise SubsystemActivationError(msg)


async def _rebuild_runtime_services(app_state: AppState) -> None:
    """Rebuild and hot-swap the runtime services.

    Invoked after provider configuration to bring the full agent runtime
    online without a process restart. Swaps the worker execution
    service, the multi-agent coordinator, and the work pipeline spine so
    ``/coordinate`` stops returning 503, the worker-callable execute
    endpoint uses the new provider, and work routing comes online.

    Raises on failure (either a typed ``RuntimeServicesBuildError`` or a
    wrapped exception) so :func:`post_setup_reinit` can keep the setup flag
    as incomplete. A half-configured runtime reporting itself as complete is
    worse than a clear error the operator can retry after fixing the
    underlying provider configuration.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        RuntimeServicesBuildError: Raised on the corresponding failure path.
    """
    from synthorg.engine.errors import (  # noqa: PLC0415
        RuntimeServicesBuildError,
    )

    try:
        from synthorg.workers.runtime_builder import (  # noqa: PLC0415
            reload_runtime_services,
        )

        # Retry cost-dial wiring BEFORE building runtime services: the
        # AgentEngine snapshots the cost forecast repo at build time, so
        # wiring afterwards would leave the rebuilt engine without the
        # forecast repo (no halt-context stamping) until yet another
        # rebuild. ``reload_runtime_services`` then threads the live
        # forecast_gate through the rebuilt entry adapters.
        forecaster = app_state.slice(BudgetStateSlice).cost_forecaster
        if (
            app_state.slice(PersistenceStateSlice).backend is not None
            and forecaster is None
        ):
            from synthorg.api._app_wiring import (  # noqa: PLC0415
                _try_wire_cost_dial,
            )

            _try_wire_cost_dial(app_state)
        await reload_runtime_services(app_state, trigger="setup")
    except MemoryError, RecursionError:
        raise
    except RuntimeServicesBuildError:
        # Already a typed domain error (logged at its origin); re-raise
        # unchanged so post_setup_reinit keeps setup_complete=false.
        raise
    except Exception as exc:
        # Critical: a provider was configured but the runtime failed to
        # wire. ERROR (not WARNING) so monitoring/operator dashboards
        # alert; wrapped in a domain error so the /setup/complete
        # controller can map it to an actionable status.
        log_exception_redacted(
            logger,
            SETUP_AGENT_BOOTSTRAP_FAILED,
            exc,
            context="runtime_services_rebuild",
        )
        msg = "Runtime services failed to rebuild after provider config"
        raise RuntimeServicesBuildError(msg) from exc
