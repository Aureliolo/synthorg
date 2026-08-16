"""Agent-related helpers for the first-run setup controller.

Handles template agent expansion, model matching, and the persistence
operations the setup controller delegates out to keep its module size
within budget.
"""

import json
from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from synthorg.api.controllers.setup_models import SetupAgentRequest, SetupAgentSummary
from synthorg.config.agent_schema import AgentConfig
from synthorg.core.domain_errors import (
    ValidationError,
)
from synthorg.core.normalization import normalize_optional_string
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_AGENT_SUMMARY_MISSING_FIELDS,
    SETUP_AGENTS_CORRUPTED,
    SETUP_AGENTS_READ_FALLBACK,
    SETUP_PRESET_NOT_FOUND,
)
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.templates.loader import LoadedTemplate
from synthorg.templates.schema import TemplateDepartmentConfig

logger = get_logger(__name__)

# Required keys every agent dict must have in the persisted list.
_REQUIRED_AGENT_KEYS: frozenset[str] = frozenset({"name", "role"})


def expand_template_agents(
    loaded: LoadedTemplate,
    locales: list[str] | None = None,
    *,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
    variables: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    """Expand a template into persistable agent dicts via the renderer.

    Renders the template through the same pipeline as the engine
    (:func:`render_template`): resolves ``extends`` / ``_remove`` / department
    head-roles and runs the shared agent expansion (auto-naming, personality
    presets, and the strategic-role model default), then projects each
    validated ``AgentConfig`` into the dict shape the matcher and persistence
    consume. Routing the wizard through the one renderer pipeline keeps it in
    lockstep with the engine -- a single source of truth for a template's
    roster, instead of a parallel load-only expansion that silently skipped
    inheritance.

    Args:
        loaded: Loaded template from the loader.
        locales: Faker locale codes for name generation.  ``None``
            uses all Latin-script locales.
        custom_presets: Optional mapping of custom preset names to
            personality config dicts (checked before builtins).
        variables: User-supplied template variable overrides (company name,
            budget, and any genuine template variables) fed to the renderer.

    Returns:
        List of agent config dicts each carrying a ``model_requirement``
        dict for downstream matching.

    Raises:
        TemplateRenderError: If rendering fails.
        TemplateValidationError: If the rendered config fails validation.
    """
    from synthorg.templates.renderer import render_template  # noqa: PLC0415

    cfg = render_template(
        loaded,
        variables=dict(variables) if variables else None,
        locales=locales,
        custom_presets=custom_presets,
    )
    return [_agent_config_to_dict(agent) for agent in cfg.agents]


def _agent_config_to_dict(agent: AgentConfig) -> dict[str, object]:
    """Project a rendered ``AgentConfig`` into a wizard agent dict.

    Returns:
        A dict with name/role/department/personality and the
        ``model_requirement`` the matcher reads; ``model`` is a blank
        placeholder ``match_and_assign_models`` overwrites.
    """
    return {
        "name": agent.name,
        "role": agent.role,
        "department": agent.department,
        "personality": agent.personality,
        "personality_preset": agent.personality_preset,
        "model_requirement": agent.model_requirement,
        "model": {"provider": "", "model_id": ""},
    }


def build_agent_config(
    data: SetupAgentRequest,
    *,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
) -> dict[str, object]:
    """Build an agent config dict for settings persistence.

    Args:
        data: Validated agent creation payload.
        custom_presets: Optional custom preset mapping.

    Returns:
        Agent configuration dict suitable for JSON serialization.

    Raises:
        ValidationError: If the personality preset name is not
            found in either custom or builtin presets.
    """
    from synthorg.templates.presets import get_personality_preset  # noqa: PLC0415

    try:
        personality_dict = get_personality_preset(
            data.personality_preset,
            custom_presets=custom_presets,
        )
    except KeyError:
        logger.warning(
            SETUP_PRESET_NOT_FOUND,
            preset=data.personality_preset,
        )
        msg = f"Unknown personality preset {data.personality_preset!r}"
        raise ValidationError(msg) from None
    agent_config: dict[str, object] = {
        "name": data.name,
        "role": data.role,
        "department": data.department,
        "personality": personality_dict,
        "personality_preset": data.personality_preset,
        "model": {
            "provider": data.model_provider,
            "model_id": data.model_id,
        },
    }
    if data.budget_limit_monthly is not None:
        agent_config["budget_limit_monthly"] = data.budget_limit_monthly
    return agent_config


async def get_existing_agents(
    settings_svc: SettingsServiceProtocol,
) -> list[dict[str, object]]:
    """Read the current agents list from settings.

    Only the "entry not found" case yields an empty list. JSON parse
    errors and non-list values are surfaced so callers do not silently
    overwrite corrupted data.

    Args:
        settings_svc: Settings service instance.

    Returns:
        List of agent config dicts (empty if entry is absent or None).

    Raises:
        ValidationError: If the stored value is not valid JSON or
            not a JSON array.
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry("company", "agents")
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        logger.debug(SETUP_AGENTS_READ_FALLBACK, reason="entry_not_found")
        return []

    if entry.source != SettingSource.DATABASE:
        logger.debug(
            SETUP_AGENTS_READ_FALLBACK,
            reason="non_database_source",
            source=entry.source,
        )
        return []

    try:
        parsed = json.loads(entry.value)
    except json.JSONDecodeError as exc:
        logger.warning(
            SETUP_AGENTS_CORRUPTED,
            reason="invalid_json",
        )
        msg = "Stored agents list is not valid JSON"
        raise ValidationError(msg) from exc

    if not isinstance(parsed, list):
        logger.warning(
            SETUP_AGENTS_CORRUPTED,
            reason="non_list_json",
            raw_type=type(parsed).__name__,
        )
        msg = f"Stored agents list is {type(parsed).__name__}, expected list"
        raise ValidationError(msg)

    _validate_agent_elements(parsed)
    return parsed


def _validate_agent_elements(parsed: list[object]) -> None:
    """Validate each element in a parsed agents list.

    Raises:
        ValidationError: If any element is not a dict with valid
            string values for required keys.
    """
    for idx, element in enumerate(parsed):
        if not isinstance(element, dict):
            logger.warning(
                SETUP_AGENTS_CORRUPTED,
                reason="non_dict_element",
                element_index=idx,
                element_type=type(element).__name__,
            )
            msg = f"Agent at index {idx} is {type(element).__name__}, expected dict"
            raise ValidationError(msg)
        if not _REQUIRED_AGENT_KEYS.issubset(element.keys()):
            logger.warning(
                SETUP_AGENTS_CORRUPTED,
                reason="missing_keys",
                element_index=idx,
                present_keys=sorted(element.keys()),
            )
            msg = f"Agent at index {idx} missing required keys (need 'name' and 'role')"
            raise ValidationError(msg)
        for key in _REQUIRED_AGENT_KEYS:
            val = element[key]
            if not isinstance(val, str) or not val.strip():
                logger.warning(
                    SETUP_AGENTS_CORRUPTED,
                    reason="invalid_field_value",
                    element_index=idx,
                    field=key,
                    value_type=type(val).__name__,
                )
                msg = f"Agent at index {idx}: '{key}' must be a non-empty string"
                raise ValidationError(msg)


def validate_agents_value(raw: str, *, strict: bool) -> bool:
    """Parse *raw* as JSON and return True if it is a non-empty list.

    When *strict* is True, raises ``ValidationError`` on corrupted
    data instead of returning False.

    Args:
        raw: Raw JSON string from settings.
        strict: When True, raise on corrupted data.

    Returns:
        True if the value is a non-empty JSON list.

    Raises:
        ValidationError: Raised on the corresponding failure path.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            SETUP_AGENTS_CORRUPTED,
            reason="invalid_json",
        )
        if strict:
            msg = "Stored agents list is not valid JSON"
            raise ValidationError(msg) from exc
        return False

    if not isinstance(parsed, list):
        logger.warning(
            SETUP_AGENTS_CORRUPTED,
            reason="non_list_json",
            raw_type=type(parsed).__name__,
        )
        if strict:
            msg = f"Stored agents list is {type(parsed).__name__}, expected list"
            raise ValidationError(msg)
        return False

    return bool(parsed)


def normalize_description(raw: str | None) -> str | None:
    """Strip whitespace from description, treating blank as None.

    Returns:
        The ``str`` value when present, ``None`` otherwise.
    """
    return normalize_optional_string(raw)


def departments_to_json(
    departments: Sequence[TemplateDepartmentConfig],
) -> str:
    """Convert template departments to a JSON string.

    Returns:
        Resulting string.
    """
    if not departments:
        return ""
    dept_list = [
        {"name": d.name, "budget_percent": d.budget_percent} for d in departments
    ]
    return json.dumps(dept_list)


def agents_to_summaries(
    agents: list[dict[str, object]],
) -> tuple[SetupAgentSummary, ...]:
    """Convert agent config dicts to summary DTOs.

    Returns:
        Tuple of the declared element types.
    """
    return tuple(agent_dict_to_summary(a) for a in agents)


def _agent_str(agent: Mapping[str, object], key: str) -> str:
    """Return the stripped string at *key*, or empty when not a string.

    Returns:
        The stripped value, or an empty string.
    """
    value = agent.get(key)
    return value.strip() if isinstance(value, str) else ""


def _agent_opt_str(value: object) -> str | None:
    """Normalise an optional string field, treating non-strings as absent.

    Returns:
        The normalised string, or ``None``.
    """
    return normalize_optional_string(value) if isinstance(value, str) else None


def agent_dict_to_summary(
    agent: dict[str, object],
) -> SetupAgentSummary:
    """Convert a single agent config dict to a summary DTO.

    Returns:
        ``SetupAgentSummary`` instance.
    """
    # Normalize string fields so whitespace-only values fall through
    # to defaults (NotBlankStr rejects blank strings).
    name = _agent_str(agent, "name") or "unknown"
    role = _agent_str(agent, "role") or "unknown"
    department = _agent_str(agent, "department") or "general"
    missing = [
        f
        for f, v in (("name", name), ("role", role), ("department", department))
        if v in ("unknown", "general") and not _agent_str(agent, f)
    ]
    if missing:
        logger.warning(
            SETUP_AGENT_SUMMARY_MISSING_FIELDS,
            missing_fields=missing,
            agent_keys=list(agent.keys()),
        )
    model = agent.get("model")
    model_dict = model if isinstance(model, dict) else {}
    # model_validate coerces the persisted string ``tier`` value against
    # SetupAgentSummary's Literal field.
    return SetupAgentSummary.model_validate(
        {
            "name": name,
            "role": role,
            "department": department,
            "model_provider": _agent_opt_str(model_dict.get("provider")),
            "model_id": _agent_opt_str(model_dict.get("model_id")),
            "capability": _agent_opt_str(model_dict.get("capability")) or "capable",
            "personality_preset": _agent_opt_str(agent.get("personality_preset")),
        },
    )
