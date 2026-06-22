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
from synthorg.engine.workspace.state import agent_workspace_root_of
from synthorg.hr.state import HrStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.setup import (
    SETUP_AGENT_BOOTSTRAP_FAILED,
    SETUP_PROVIDER_RELOAD_FAILED,
)
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.management._persistence import resolve_retry_max_attempts
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

# Module-level lock: serializes read-modify-write on agents settings.
AGENT_LOCK = asyncio.Lock()

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
        resolver = config_resolver_of(app_state)
        provider_configs = await resolver.get_provider_configs()
        if provider_configs:
            retry_max_attempts = await resolve_retry_max_attempts(resolver)
            new_registry = ProviderRegistry.from_config(
                provider_configs,
                retry_max_attempts=retry_max_attempts,
            )
            app_state.swap_provider_registry(new_registry)
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
            build_runtime_services,
        )

        # Retry cost-dial wiring BEFORE building runtime services: the
        # AgentEngine snapshots the cost forecast repo at build time, so
        # wiring afterwards would leave the rebuilt engine without the
        # forecast repo (no halt-context stamping) until yet another
        # rebuild. The entry-adapter rebuild below then threads the live
        # forecast_gate through.
        forecaster = app_state.slice(BudgetStateSlice).cost_forecaster
        if (
            app_state.slice(PersistenceStateSlice).backend is not None
            and forecaster is None
        ):
            from synthorg.api._app_wiring import (  # noqa: PLC0415
                _try_wire_cost_dial,
            )

            _try_wire_cost_dial(app_state)
        services = await build_runtime_services(
            app_state,
            workspace_root=agent_workspace_root_of(app_state),
        )
        app_state.swap_worker_execution_service(
            services.worker_execution_service,
        )
        if services.coordinator is not None:
            app_state.swap_coordinator(services.coordinator)
        if services.work_pipeline is not None:
            app_state.swap_work_pipeline(services.work_pipeline)
        from synthorg.engine.pipeline.entry.boot import (  # noqa: PLC0415
            wire_real_intake_entry,
            wire_real_objective_entry,
            wire_real_task_board_entry,
        )

        await wire_real_intake_entry(app_state, hot_swap=True)
        await wire_real_objective_entry(app_state, hot_swap=True)
        await wire_real_task_board_entry(app_state, hot_swap=True)
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
