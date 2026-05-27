"""Agent-side setup helpers: validation, bootstrap, tier coverage, embedder selection.

Covers agent-array shape, post-setup reload, agent bootstrap,
template-driven agent creation, provider-tier validation, and
embedder auto-selection. Company-side helpers (locales, templates,
password length) live in ``setup.company_helpers``.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any

from synthorg.api.controllers.setup.company_helpers import read_name_locales
from synthorg.api.controllers.setup_agents import (
    agents_to_summaries,
    expand_template_agents,
    match_and_assign_models,
    validate_agents_value,
)
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.auth.roles import HumanRole
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    NotFoundError,
    ProviderTierCoverageInsufficientError,
)
from synthorg.engine.workspace.state import agent_workspace_root_of
from synthorg.hr.state import HrStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.setup import (
    SETUP_AGENT_BOOTSTRAP_FAILED,
    SETUP_AGENT_INDEX_OUT_OF_RANGE,
    SETUP_MODEL_ID_COLLECTION_ERROR,
    SETUP_PROVIDER_RELOAD_FAILED,
    SETUP_PROVIDER_TIER_COVERAGE_INSUFFICIENT,
    SETUP_STATUS_SETTINGS_DEFAULT_USED,
    SETUP_STATUS_SETTINGS_UNAVAILABLE,
)
from synthorg.persistence.state import PersistenceStateSlice, persistence_of
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import provider_management_of
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.api.controllers.setup_models import SetupAgentSummary
    from synthorg.api.state import AppState
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.settings.service import SettingsService
    from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Inverted-convention result from ``auto_select_embedder``: ``None``
# means success (a model was ranked and persisted); a ``str`` carries
# the human-readable failure reason. Aliased here so the call site
# can pass the result directly to
# ``SetupCompleteResponse.embedder_failure_reason`` without re-stating
# the inversion at every call.
type EmbedderSelectResult = str | None

# Module-level lock: serializes read-modify-write on agents settings.
AGENT_LOCK = asyncio.Lock()

# Module-level lock: serializes the entire /setup/complete flow so two
# concurrent clients cannot both pass the ``setup_complete=false`` check
# and then race on reinit + flag write.
COMPLETE_LOCK = asyncio.Lock()


def validate_agent_index(
    agent_index: int,
    agents: list[dict[str, Any]],
) -> None:
    """Raise ``NotFoundError`` if *agent_index* is out of range.

    Raises:
        NotFoundError: Raised on the corresponding failure path.
    """
    if agent_index < 0 or agent_index >= len(agents):
        if not agents:
            msg = f"Agent index {agent_index} out of range (no agents configured)"
        else:
            msg = f"Agent index {agent_index} out of range (0-{len(agents) - 1})"
        logger.warning(
            SETUP_AGENT_INDEX_OUT_OF_RANGE,
            agent_index=agent_index,
            agent_count=len(agents),
        )
        raise NotFoundError(msg)


async def post_setup_reinit(app_state: AppState) -> None:
    """Reload providers and bootstrap agents after setup completion.

    Raises on failure so the caller can keep ``setup_complete=false``
    when reinit cannot finish; a half-configured runtime presenting
    itself as "complete" is worse than a clear error the operator can
    retry after fixing the underlying provider config.

    The matching call site in
    :func:`SetupController.complete_setup` only persists the completion
    flag when this function returns without raising.

    Args:
        app_state: Application state containing services.

    Raises:
        Exception: Raised on the corresponding failure path.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return

    # 1. Reload provider registry from persisted config.
    try:
        provider_configs = await config_resolver_of(app_state).get_provider_configs()
        if provider_configs:
            new_registry = ProviderRegistry.from_config(
                provider_configs,
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


async def check_needs_admin(
    persistence: PersistenceBackend,
) -> bool:
    """Return True if no CEO-role user exists.

    Fail-open on non-critical lookup errors; interpreter-critical
    errors propagate via ``reraise_critical``.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    count: int | None = None
    try:
        count = await persistence.users.count_by_role(HumanRole.CEO)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            context="admin_count",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return True
    return count == 0 if count is not None else True


async def check_needs_setup(
    settings_svc: SettingsService,
) -> bool:
    """Return True if setup is still needed (fail-open on error).

    Returns:
        ``True`` or ``False`` reflecting the condition.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry(
            "api",
            "setup_complete",
        )
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return True
    except Exception:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
        )
        return True
    else:
        return entry.value != "true"


async def check_has_agents(
    settings_svc: SettingsService,
    *,
    strict: bool = False,
) -> bool:
    """Check whether any agents have been explicitly created.

    Args:
        settings_svc: Settings service instance.
        strict: When True, propagate parsing exceptions.

    Returns:
        True if user-created agents exist.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        Exception: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry("company", "agents")
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        logger.debug(
            SETUP_STATUS_SETTINGS_DEFAULT_USED,
            setting="agents",
        )
        return False
    except Exception:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="agents",
        )
        if strict:
            raise
        return False

    if entry.source != SettingSource.DATABASE:
        logger.debug(
            SETUP_STATUS_SETTINGS_DEFAULT_USED,
            setting="agents",
            source=entry.source,
        )
        return False
    if not entry.value:
        return False
    return validate_agents_value(entry.value, strict=strict)


def _validate_tier_coverage(providers: Mapping[str, Any]) -> None:
    """Reject provider sets that cannot satisfy tier classification.

    The model matcher tolerates fewer than three models per provider
    by returning all models for every tier in that case, so this gate
    only blocks the truly empty case: zero models across all
    registered providers. Setups with a couple of models continue
    to work; the matcher just assigns the same model to every tier.

    Args:
        providers: Provider name -> config mapping resolved from
            ``provider_management.list_providers()``.

    Raises:
        ProviderTierCoverageInsufficientError: When NO models are
            available across the registered providers. The frontend
            reads ``error_detail.error_code`` (2004) to surface a
            "Go back to Providers step" affordance instead of a
            generic Retry button.
    """
    total_models = sum(len(getattr(cfg, "models", ())) for cfg in providers.values())
    if total_models > 0:
        return
    msg = (
        "No configured provider exposes any models. Go back to the "
        "Providers step, add at least one model to a provider, then "
        "return here to apply the template."
    )
    logger.warning(
        SETUP_PROVIDER_TIER_COVERAGE_INSUFFICIENT,
        provider_count=len(providers),
        total_model_count=0,
    )
    raise ProviderTierCoverageInsufficientError(msg)


async def auto_create_template_agents(
    template: CompanyTemplate,
    app_state: AppState,
    settings_svc: SettingsService,
) -> tuple[SetupAgentSummary, ...]:
    """Expand template agents, match models, persist, and return summaries.

    Returns:
        Tuple of the declared element types.
    """
    from synthorg.templates.model_matcher import ModelMatcherConfig  # noqa: PLC0415
    from synthorg.templates.preset_service import (  # noqa: PLC0415
        fetch_custom_presets_map,
    )

    async def _resolve_matcher_config() -> ModelMatcherConfig | None:
        """Resolve matcher config; degrade to None on resolution failure.

        Non-critical bridge-config resolution failures (missing
        setting, validation error, persistence flake) AND projection
        failures (``from_bridge_config`` raising on a tampered field)
        must both keep the template bootstrap alive; interpreter-
        critical errors propagate via ``reraise_critical``. Mirrors the
        fail-open pattern used by ``post_setup_reinit``.

        Returns:
            The ``ModelMatcherConfig`` value when present, ``None`` otherwise.
        """
        if app_state.slice(SettingsStateSlice).config_resolver is None:
            return None
        try:
            bridge_cfg = await config_resolver_of(app_state).get_engine_bridge_config()
            return ModelMatcherConfig.from_bridge_config(bridge_cfg)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETUP_STATUS_SETTINGS_UNAVAILABLE,
                context="matcher_config",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async with asyncio.TaskGroup() as tg:
        loc_task = tg.create_task(read_name_locales(settings_svc))
        preset_task = tg.create_task(
            fetch_custom_presets_map(persistence_of(app_state).custom_presets),
        )
        prov_task = tg.create_task(
            provider_management_of(app_state).list_providers(),
        )
        matcher_task = tg.create_task(_resolve_matcher_config())
    locales = loc_task.result()
    custom_presets = preset_task.result()
    agents = expand_template_agents(
        template,
        locales=locales,
        custom_presets=custom_presets,
    )
    providers = prov_task.result()
    _validate_tier_coverage(providers)
    matcher_config = matcher_task.result()
    agents = match_and_assign_models(agents, providers, matcher_config)

    async with AGENT_LOCK:
        await settings_svc.set(
            "company",
            "agents",
            json.dumps(agents),
        )

    return agents_to_summaries(agents)


async def collect_model_ids(app_state: AppState) -> tuple[str, ...]:
    """Extract model IDs from provider configs for embedding selection.

    Best-effort: returns an empty tuple if config resolver is not
    available or provider configs cannot be read for a non-critical
    reason; interpreter-critical errors propagate via
    ``reraise_critical``.

    Returns:
        Tuple of the declared element types.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return ()
    try:
        configs = await config_resolver_of(app_state).get_provider_configs()
        ids: list[str] = [
            str(model.id) for pc in configs.values() for model in pc.models
        ]
        return tuple(ids)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            SETUP_MODEL_ID_COLLECTION_ERROR,
            check="collect_model_ids",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()


async def auto_select_embedder(
    *,
    settings_svc: SettingsService,
    available_model_ids: tuple[str, ...],
    provider_preset_name: str | None = None,
    has_gpu: bool | None = None,
) -> EmbedderSelectResult:
    """Auto-select an embedding model and persist the choice.

    Best-effort: logs a warning but does not raise on failure.
    Called during setup completion after providers are validated.

    Args:
        settings_svc: Settings service for persisting the selection.
        available_model_ids: Model IDs discovered from providers.
        provider_preset_name: Provider preset for tier inference.
        has_gpu: Whether the host has a GPU.

    Returns:
        ``None`` on success (a model was ranked and persisted), or a
        short human-readable failure reason string when selection or
        persistence failed. The inverted convention (None = success,
        str = failure) keeps the caller free to pass the result
        directly to ``SetupCompleteResponse.embedder_failure_reason``.
    """
    from synthorg.memory.embedding.selector import (  # noqa: PLC0415
        infer_deployment_tier,
        select_embedding_model,
    )
    from synthorg.observability.events.memory import (  # noqa: PLC0415
        MEMORY_EMBEDDER_AUTO_SELECT_FAILED,
        MEMORY_EMBEDDER_AUTO_SELECTED,
    )

    tier = infer_deployment_tier(
        provider_preset_name,
        has_gpu=has_gpu,
    )
    ranking = select_embedding_model(
        available_model_ids,
        deployment_tier=tier,
    )
    if ranking is None:
        # Try without tier filter as fallback.
        ranking = select_embedding_model(available_model_ids)
    if ranking is None:
        reason = "no ranked embedding model available for configured providers"
        logger.warning(
            MEMORY_EMBEDDER_AUTO_SELECT_FAILED,
            available_models=len(available_model_ids),
            tier=tier.value,
            reason=reason,
        )
        return reason
    try:
        await settings_svc.set(
            "memory",
            "embedder_model",
            ranking.model_id,
        )
        await settings_svc.set(
            "memory",
            "embedder_dims",
            str(ranking.output_dims),
        )
    except Exception as exc:
        reraise_critical(exc)
        reason = "failed to persist embedder settings"
        logger.warning(
            MEMORY_EMBEDDER_AUTO_SELECT_FAILED,
            reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return reason
    # INFO log emitted AFTER the persistence writes succeed so the
    # event accurately reflects committed state. A pre-write log
    # would otherwise misleadingly claim success when the writes
    # below fail and fall through to the warning branch.
    logger.info(
        MEMORY_EMBEDDER_AUTO_SELECTED,
        model_id=ranking.model_id,
        tier=tier.value,
        overall_score=ranking.overall,
        dims=ranking.output_dims,
    )
    return None
