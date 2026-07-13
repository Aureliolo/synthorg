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

    # 4. Rewire provider-gated features that a boot-empty start could not wire
    #    (charter needs a provider for its interview LLM), so they come online
    #    without a restart now that a provider exists.
    await _rewire_post_setup_features(app_state)


async def _rewire_post_setup_features(app_state: AppState) -> None:
    """Rewire provider-gated features a boot-empty start could not wire.

    The charter engine, research subsystem, and knowledge substrate wire
    only behind a configured provider (and, for research, a now-filled
    model), so an app that booted with no provider leaves their endpoints
    unavailable until a restart. The wiring is idempotent (a no-op when
    already wired), so re-running it after the provider reload + model
    auto-fill brings them online live.

    Raises on failure, like the other reinit steps, so ``post_setup_reinit``
    keeps ``setup_complete=false`` rather than reporting a half-configured
    runtime as complete. Expected degradation (provider or memory substrate
    absent) does NOT raise here: ``_wire_charter_engine`` swallows it internally
    and logs ``CHARTER_SUBSTRATE_UNAVAILABLE``, so setup still completes with
    charter endpoints 503-ing, exactly as at boot. Only a genuinely broken
    rewire (e.g. a settings read that fails) propagates and aborts completion.

    Raises:
        Exception: Re-raised after logging so completion is not persisted.
    """
    from synthorg.api.lifecycle_helpers.charter_wiring import (  # noqa: PLC0415
        _wire_charter_engine,
    )
    from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
        wire_chief_of_staff_proposer,
        wire_conversational_actor,
    )
    from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
        _wire_chief_of_staff_chat,
        _wire_research_engine,
    )
    from synthorg.api.lifecycle_helpers.kanban_wiring import (  # noqa: PLC0415
        wire_kanban_board,
    )
    from synthorg.api.lifecycle_helpers.knowledge_wiring import (  # noqa: PLC0415
        wire_knowledge_engine,
    )
    from synthorg.api.lifecycle_helpers.narrative_wiring import (  # noqa: PLC0415
        wire_run_narrator,
    )
    from synthorg.api.lifecycle_helpers.plan_review_wiring import (  # noqa: PLC0415
        wire_plan_review_gate,
        wire_plan_review_panel,
    )
    from synthorg.api.lifecycle_helpers.refinement_wiring import (  # noqa: PLC0415
        wire_refinement_router,
    )
    from synthorg.api.lifecycle_helpers.sprint_wiring import (  # noqa: PLC0415
        wire_sprint_service,
    )
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

    try:
        settings_service = app_state.slice(SettingsStateSlice).settings_service
        si_config = await load_self_improvement_config(settings_service)
        registry = app_state.slice(ProvidersStateSlice).registry
        persistence = app_state.slice(PersistenceStateSlice).backend
        cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
        approval_store = app_state.slice(ApprovalStateSlice).store
        await _wire_charter_engine(
            app_state,
            provider_registry=registry,
            persistence=persistence,
            cost_tracker=cost_tracker,
            si_config=si_config,
        )
        # Research + knowledge are on by default and need a provider /
        # memory substrate that a boot-empty start lacked; their wiring is
        # idempotent, so re-running it brings them online live now that
        # setup has filled their models and a provider exists.
        await _wire_research_engine(app_state, provider_registry=registry)
        await wire_knowledge_engine(app_state, provider_registry=registry)
        # The Chief-of-Staff trio (chat / narrator / propose) wires only behind
        # a resolvable per-feature model. A boot-empty start left their models
        # blank, so they stayed unwired; setup has now provisioned real models,
        # and their wiring is idempotent, so re-running it here brings them
        # online with no restart (mirroring the boot order: narrator, then the
        # proposer + the refinement router and conversational actor built on it).
        await _wire_chief_of_staff_chat(
            app_state,
            provider_registry=registry,
            cost_tracker=cost_tracker,
            si_config=si_config,
        )
        await wire_run_narrator(
            app_state,
            provider_registry=registry,
            cost_tracker=cost_tracker,
            si_config=si_config,
        )
        if approval_store is not None:
            await wire_chief_of_staff_proposer(
                app_state,
                provider_registry=registry,
                persistence=persistence,
                cost_tracker=cost_tracker,
                effective_approval_store=approval_store,
                si_config=si_config,
            )
            await wire_refinement_router(app_state)
            await wire_conversational_actor(app_state, si_config=si_config)
        await wire_plan_review_gate(app_state)
        await wire_plan_review_panel(
            app_state,
            provider_registry=registry,
            cost_tracker=cost_tracker,
        )
        await wire_sprint_service(app_state)
        await wire_kanban_board(app_state)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            SETUP_FEATURE_REWIRE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise


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
        await reload_runtime_services(app_state)
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
