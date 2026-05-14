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
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import (
    NotFoundError,
    ProviderTierCoverageInsufficientError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_AGENT_BOOTSTRAP_FAILED,
    SETUP_AGENT_INDEX_OUT_OF_RANGE,
    SETUP_MODEL_ID_COLLECTION_ERROR,
    SETUP_PROVIDER_RELOAD_FAILED,
    SETUP_PROVIDER_TIER_COVERAGE_INSUFFICIENT,
    SETUP_STATUS_SETTINGS_DEFAULT_USED,
    SETUP_STATUS_SETTINGS_UNAVAILABLE,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.api.controllers.setup_models import SetupAgentSummary
    from synthorg.api.state import AppState
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.settings.service import SettingsService
    from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Module-level lock: serializes read-modify-write on agents settings.
AGENT_LOCK = asyncio.Lock()


def validate_agent_index(
    agent_index: int,
    agents: list[dict[str, Any]],
) -> None:
    """Raise ``NotFoundError`` if *agent_index* is out of range."""
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

    Both operations are non-fatal: setup completion must succeed
    even if re-init partially fails (the user can restart the
    server to pick up changes).

    Args:
        app_state: Application state containing services.
    """
    if not app_state.has_config_resolver:
        return

    # 1. Reload provider registry from persisted config.
    try:
        provider_configs = await app_state.config_resolver.get_provider_configs()
        if provider_configs:
            new_registry = ProviderRegistry.from_config(
                provider_configs,
            )
            app_state.swap_provider_registry(new_registry)
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            SETUP_PROVIDER_RELOAD_FAILED,
            error="Provider reload failed after setup (non-fatal)",
        )

    # 2. Bootstrap agents into runtime registry.
    if app_state.has_agent_registry:
        try:
            from synthorg.api.bootstrap import (  # noqa: PLC0415
                bootstrap_agents,
            )

            await bootstrap_agents(
                config_resolver=app_state.config_resolver,
                agent_registry=app_state.agent_registry,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                SETUP_AGENT_BOOTSTRAP_FAILED,
                error="Agent bootstrap failed (non-fatal)",
            )


async def check_needs_admin(
    persistence: PersistenceBackend,
) -> bool:
    """Return True if no CEO-role user exists (fail-open on error)."""
    count: int | None = None
    try:
        count = await persistence.users.count_by_role(HumanRole.CEO)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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
    """Return True if setup is still needed (fail-open on error)."""
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
    """Expand template agents, match models, persist, and return summaries."""
    from synthorg.templates.model_matcher import ModelMatcherConfig  # noqa: PLC0415
    from synthorg.templates.preset_service import (  # noqa: PLC0415
        fetch_custom_presets_map,
    )

    async def _resolve_matcher_config() -> ModelMatcherConfig | None:
        """Resolve matcher config; degrade to None on resolution failure.

        Bridge-config resolution failures (missing setting, validation
        error, persistence flake) AND projection failures
        (``from_bridge_config`` raising on a tampered field) must both
        keep the template bootstrap alive. Mirrors the fail-open
        pattern used by ``post_setup_reinit``.
        """
        if not app_state.has_config_resolver:
            return None
        try:
            bridge_cfg = await app_state.config_resolver.get_engine_bridge_config()
            return ModelMatcherConfig.from_bridge_config(bridge_cfg)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
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
            fetch_custom_presets_map(app_state.persistence.custom_presets),
        )
        prov_task = tg.create_task(
            app_state.provider_management.list_providers(),
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
    available or provider configs cannot be read.
    """
    if not app_state.has_config_resolver:
        return ()
    try:
        configs = await app_state.config_resolver.get_provider_configs()
        ids: list[str] = [
            str(model.id) for pc in configs.values() for model in pc.models
        ]
        return tuple(ids)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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
) -> None:
    """Auto-select an embedding model and persist the choice.

    Best-effort: logs a warning but does not raise on failure.
    Called during setup completion after providers are validated.

    Args:
        settings_svc: Settings service for persisting the selection.
        available_model_ids: Model IDs discovered from providers.
        provider_preset_name: Provider preset for tier inference.
        has_gpu: Whether the host has a GPU.
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
        logger.warning(
            MEMORY_EMBEDDER_AUTO_SELECT_FAILED,
            available_models=len(available_model_ids),
            tier=tier.value,
            reason="no LMEB-ranked model in available models",
        )
        return
    logger.info(
        MEMORY_EMBEDDER_AUTO_SELECTED,
        model_id=ranking.model_id,
        tier=tier.value,
        overall_score=ranking.overall,
        dims=ranking.output_dims,
    )
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
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            MEMORY_EMBEDDER_AUTO_SELECT_FAILED,
            reason="failed to persist embedder settings",
        )
