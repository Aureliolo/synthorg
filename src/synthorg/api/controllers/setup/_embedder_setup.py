# module-kind: code
"""Embedder auto-selection and template-driven agent creation.

Expands template agents, matches models to tiers, persists the agent
array, collects provider model IDs, and ranks an embedding model for
the memory subsystem. The agents-settings write reuses the shared
``AGENT_LOCK`` so it serialises against the setup controllers'
read-modify-write paths.
"""

import asyncio
import json
from collections.abc import Mapping

from synthorg.api.controllers.setup._runtime_wiring import AGENT_LOCK
from synthorg.api.controllers.setup.company_helpers import read_name_locales
from synthorg.api.controllers.setup_agents import (
    agents_to_summaries,
    expand_template_agents,
    match_and_assign_models,
)
from synthorg.api.controllers.setup_models import SetupAgentSummary
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ProviderTierCoverageInsufficientError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_MODEL_ID_COLLECTION_ERROR,
    SETUP_PROVIDER_TIER_COVERAGE_INSUFFICIENT,
    SETUP_STATUS_SETTINGS_UNAVAILABLE,
)
from synthorg.persistence.state import persistence_of
from synthorg.providers.state import provider_management_of
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice, config_resolver_of
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Inverted-convention result from ``auto_select_embedder``: ``None``
# means success (a model was ranked and persisted); a ``str`` carries
# the human-readable failure reason. Aliased here so the call site
# can pass the result directly to
# ``SetupCompleteResponse.embedder_failure_reason`` without re-stating
# the inversion at every call.
type EmbedderSelectResult = str | None


def _validate_tier_coverage(providers: Mapping[str, object]) -> None:
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
