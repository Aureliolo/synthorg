"""Private helper functions for the setup controller.

Extracted from ``setup.py`` to keep the controller under the 800-line
limit.  These are internal utilities -- not public API.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any, NamedTuple

from synthorg.api.auth.config import AuthConfig
from synthorg.api.controllers.setup_agents import (
    agents_to_summaries,
    departments_to_json,
    expand_template_agents,
    match_and_assign_models,
    validate_agents_value,
)
from synthorg.api.controllers.setup_models import (
    SetupAgentSummary,  # noqa: TC001
)
from synthorg.api.errors import (
    ApiValidationError,
    ConflictError,
    NotFoundError,
)
from synthorg.api.guards import HumanRole
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_AGENT_BOOTSTRAP_FAILED,
    SETUP_AGENT_INDEX_OUT_OF_RANGE,
    SETUP_ALREADY_COMPLETE,
    SETUP_COMPLETE_CHECK_ERROR,
    SETUP_NAME_LOCALES_CORRUPTED,
    SETUP_NAME_LOCALES_INVALID,
    SETUP_PROVIDER_RELOAD_FAILED,
    SETUP_STATUS_SETTINGS_DEFAULT_USED,
    SETUP_STATUS_SETTINGS_UNAVAILABLE,
    SETUP_TEMPLATE_INVALID,
    SETUP_TEMPLATE_NOT_FOUND,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError

if TYPE_CHECKING:
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.settings.service import SettingsService
    from synthorg.templates.loader import LoadedTemplate
    from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Module-level lock: serializes read-modify-write on agents settings.
AGENT_LOCK = asyncio.Lock()

# Derive from AuthConfig default to prevent silent divergence.
DEFAULT_MIN_PASSWORD_LENGTH: int = AuthConfig.model_fields[
    "min_password_length"
].default


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
            exc_info=True,
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
                exc_info=True,
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
            error=str(exc),
            exc_info=True,
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
            exc_info=True,
        )
        return True
    else:
        return entry.value != "true"


async def check_has_company(
    settings_svc: SettingsService,
    *,
    strict: bool = False,
) -> bool:
    """Check whether a company name has been explicitly created.

    Args:
        settings_svc: Settings service instance.
        strict: When True, propagate unexpected exceptions.

    Returns:
        True if a user-created company name exists.
    """
    try:
        entry = await settings_svc.get_entry(
            "company",
            "company_name",
        )
        if entry.source != SettingSource.DATABASE:
            logger.debug(
                SETUP_STATUS_SETTINGS_DEFAULT_USED,
                setting="company_name",
                source=entry.source,
            )
            return False
        return bool(entry.value and entry.value.strip())
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        logger.debug(
            SETUP_STATUS_SETTINGS_DEFAULT_USED,
            setting="company_name",
        )
        return False
    except Exception:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="company_name",
            exc_info=True,
        )
        if strict:
            raise
        return False


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
            exc_info=True,
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


def validate_locale_selection(
    locales: list[str],
    sentinel: str,
    valid_codes: frozenset[str],
) -> None:
    """Validate locale selection, raising on invalid input.

    Args:
        locales: User-submitted locale codes.
        sentinel: The "all locales" sentinel value.
        valid_codes: Set of valid locale codes.

    Raises:
        ApiValidationError: On mixed sentinel or invalid codes.
    """
    if sentinel in locales and len(locales) > 1:
        msg = f"'{sentinel}' cannot be combined with explicit locale codes"
        logger.warning(
            SETUP_NAME_LOCALES_INVALID,
            reason="mixed_sentinel",
        )
        raise ApiValidationError(msg)
    invalid = [loc for loc in locales if loc != sentinel and loc not in valid_codes]
    if invalid:
        logger.warning(
            SETUP_NAME_LOCALES_INVALID,
            invalid_locales=invalid,
        )
        msg = f"Invalid locale codes: {invalid}"
        raise ApiValidationError(msg)
    unique = list(dict.fromkeys(locales))
    if len(unique) != len(locales):
        logger.warning(
            SETUP_NAME_LOCALES_INVALID,
            reason="duplicates",
        )
        msg = "Duplicate locale codes are not allowed"
        raise ApiValidationError(msg)


async def check_has_name_locales(
    settings_svc: SettingsService,
) -> bool:
    """Check whether name locales have been configured.

    Args:
        settings_svc: Settings service instance.

    Returns:
        True if name locales are user-configured.
    """
    try:
        entry = await settings_svc.get_entry(
            "company",
            "name_locales",
        )
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return False
    except Exception:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="name_locales",
            exc_info=True,
        )
        return False
    if entry.source != SettingSource.DATABASE or not entry.value:
        return False
    parsed = parse_locale_json(entry.value)
    return parsed is not None and len(parsed) > 0


async def resolve_min_password_length(
    settings_svc: SettingsService,
) -> int:
    """Resolve the minimum password length from settings.

    Args:
        settings_svc: Settings service instance.

    Returns:
        Resolved minimum password length.
    """
    raw_pw_value: str | None = None
    try:
        pw_entry = await settings_svc.get_entry(
            "api",
            "min_password_length",
        )
        raw_pw_value = pw_entry.value
        parsed = int(raw_pw_value)
        return max(parsed, DEFAULT_MIN_PASSWORD_LENGTH)
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        logger.debug(
            SETUP_STATUS_SETTINGS_DEFAULT_USED,
            setting="min_password_length",
        )
    except ValueError:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="min_password_length",
            reason="non_integer_value",
            raw=raw_pw_value,
        )
    except Exception:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="min_password_length",
            exc_info=True,
        )
    return DEFAULT_MIN_PASSWORD_LENGTH


async def check_setup_not_complete(
    settings_svc: SettingsService,
) -> None:
    """Raise ConflictError if setup has already been completed."""
    is_complete = await is_setup_complete(settings_svc)
    if is_complete:
        logger.warning(SETUP_ALREADY_COMPLETE)
        msg = "Setup has already been completed"
        raise ConflictError(msg)


async def auto_create_template_agents(
    template: CompanyTemplate,
    app_state: AppState,
    settings_svc: SettingsService,
) -> tuple[SetupAgentSummary, ...]:
    """Expand template agents, match models, persist, and return summaries."""
    from synthorg.templates.preset_service import (  # noqa: PLC0415
        fetch_custom_presets_map,
    )

    async with asyncio.TaskGroup() as tg:
        loc_task = tg.create_task(read_name_locales(settings_svc))
        preset_task = tg.create_task(
            fetch_custom_presets_map(app_state.persistence.custom_presets),
        )
        prov_task = tg.create_task(
            app_state.provider_management.list_providers(),
        )
    locales = loc_task.result()
    custom_presets = preset_task.result()
    agents = expand_template_agents(
        template,
        locales=locales,
        custom_presets=custom_presets,
    )
    providers = prov_task.result()
    agents = match_and_assign_models(agents, providers)

    async with AGENT_LOCK:
        await settings_svc.set(
            "company",
            "agents",
            json.dumps(agents),
        )

    return agents_to_summaries(agents)


def parse_locale_json(raw: str) -> list[str] | None:
    """Parse and validate a JSON-encoded locale list.

    Returns a ``list`` on success, or ``None`` when invalid.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, TypeError:
        logger.warning(
            SETUP_NAME_LOCALES_CORRUPTED,
            reason="invalid_json_or_type",
            raw=raw[:200] if isinstance(raw, str) and raw else None,
        )
        return None
    if not isinstance(parsed, list):
        logger.warning(
            SETUP_NAME_LOCALES_CORRUPTED,
            reason="expected_list",
            actual_type=type(parsed).__name__,
        )
        return None
    return parsed


async def read_name_locales(
    settings_svc: SettingsService,
    *,
    resolve: bool = True,
) -> list[str] | None:
    """Read stored name locale preference.

    Args:
        settings_svc: Settings service instance.
        resolve: When True, expand sentinels to concrete codes.

    Returns:
        Locale codes, or None when absent/unparseable.
    """
    try:
        entry = await settings_svc.get_entry(
            "company",
            "name_locales",
        )
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return None
    except Exception:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="name_locales",
            exc_info=True,
        )
        return None
    if not entry.value:
        return None
    parsed = parse_locale_json(entry.value)
    if parsed is None:
        return None
    if resolve:
        from synthorg.templates.locales import (  # noqa: PLC0415
            resolve_locales,
        )

        parsed = resolve_locales(parsed)
    return parsed or None


async def is_setup_complete(
    settings_svc: SettingsService,
) -> bool:
    """Check whether setup has been completed.

    Args:
        settings_svc: Settings service instance.

    Returns:
        True if setup_complete is "true".

    Raises:
        Exception: Propagates unexpected errors after logging.
    """
    try:
        entry = await settings_svc.get_entry(
            "api",
            "setup_complete",
        )
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return False
    except Exception:
        logger.error(
            SETUP_COMPLETE_CHECK_ERROR,
            exc_info=True,
        )
        raise
    else:
        return entry.value == "true"


class TemplateResult(NamedTuple):
    """Result of template resolution."""

    departments_json: str
    department_count: int
    template_applied: str | None
    template: CompanyTemplate | None


def resolve_template(template_name: str | None) -> TemplateResult:
    """Validate template and extract department data."""
    if template_name is None:
        return TemplateResult("", 0, None, None)

    loaded = load_template_safe(template_name)
    departments_json = departments_to_json(
        loaded.template.departments,
    )
    return TemplateResult(
        departments_json,
        len(loaded.template.departments),
        template_name,
        loaded.template,
    )


async def persist_company_settings(
    settings_svc: SettingsService,
    company_name: str,
    description: str | None,
    departments_json: str,
) -> None:
    """Write company name, description, and departments."""
    await settings_svc.set(
        "company",
        "company_name",
        company_name,
    )
    await settings_svc.set(
        "company",
        "description",
        description or "",
    )
    await settings_svc.set(
        "company",
        "departments",
        departments_json or "[]",
    )


def load_template_safe(template_name: str) -> LoadedTemplate:
    """Load a template by name with API-friendly error handling.

    Args:
        template_name: Template name to load.

    Returns:
        ``LoadedTemplate`` instance.

    Raises:
        NotFoundError: If the template does not exist.
        ApiValidationError: If it fails to render or validate.
    """
    from synthorg.templates.errors import (  # noqa: PLC0415
        TemplateNotFoundError,
        TemplateRenderError,
        TemplateValidationError,
    )
    from synthorg.templates.loader import (  # noqa: PLC0415
        load_template,
    )

    try:
        return load_template(template_name)
    except TemplateNotFoundError as exc:
        msg = f"Template {template_name!r} not found"
        logger.warning(
            SETUP_TEMPLATE_NOT_FOUND,
            template=template_name,
        )
        raise NotFoundError(msg) from exc
    except (TemplateRenderError, TemplateValidationError) as exc:
        msg = f"Template {template_name!r} is invalid: {exc}"
        logger.warning(
            SETUP_TEMPLATE_INVALID,
            template=template_name,
            error=str(exc),
        )
        raise ApiValidationError(msg) from exc


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
            SETUP_COMPLETE_CHECK_ERROR,
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
            exc_info=True,
        )
